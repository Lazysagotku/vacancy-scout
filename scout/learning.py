# -*- coding: utf-8 -*-
"""Обратная связь по откликам: чем ответил hh и что из этого следует.

Пороги отправки до сих пор подбирались на глаз: балл выше шестидесяти, мнение
положительное. Проверить эти числа нечем - журнал знает, сколько ушло, и не
знает, что сработало.

Модуль закрывает петлю. `collect` читает страницу откликов hh и проставляет
каждому отправленному отклику исход. `report` считает долю ответов по
признакам, которые разведчик выбирал сам: балл, мнение, трек, резюме, длина
письма, рейтинг работодателя. По этим долям пороги двигаются на данных.

Сетевой работы в `report` нет: он считает по журналу и базе находок, поэтому
проверяется тестами без браузера.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from scout import store

log = logging.getLogger(__name__)

# Как подписи на странице откликов hh ложатся на исходы. Порядок проверки
# решает дважды. Приглашение идёт раньше просмотра: у приглашения карточка
# помечена просмотренной тоже. А «не просмотрен» идёт раньше «просмотрен»,
# потому что второе лежит внутри первого подстрокой - на этой же грабле
# разведчик уже спотыкался, когда «ии» находилось внутри «организации».
LABELS = (
    ("invited", ("приглашение", "собеседование", "выход на работу")),
    ("rejected", ("отказ",)),
    ("silent", ("не просмотрен", "ожидание")),
    ("viewed", ("просмотрен",)),
)

# Минимальный размер группы, при котором доля ответов что-то значит. Три
# отклика из четырёх - это не 75%, это совпадение.
MIN_GROUP = 8


def classify_label(text: str) -> str:
    """Исход по подписи карточки на странице откликов."""
    lowered = (text or "").casefold()
    for outcome, words in LABELS:
        if any(word in lowered for word in words):
            return outcome
    return "silent"


def collect(limit_pages: int = 5, headless: bool = True) -> dict:
    """Прочитать исходы отправленных откликов со страницы hh.

    Берём только те вакансии, которые есть в журнале отправленного: чужие
    отклики, сделанные руками или автоматикой hh, разведчика не касаются.
    """
    from playwright.sync_api import sync_playwright

    from scout.collector import PROFILE, CollectError, hit_vpn_check

    ours = {row["vacancy_id"]: row for row in store.sent_applications()}
    if not ours:
        return {"read": 0, "updated": 0, "note": "журнал отправленного пуст"}

    seen: dict[str, str] = {}
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1380, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for number in range(max(1, limit_pages)):
                page.goto(f"https://hh.ru/applicant/negotiations?page={number}",
                          wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(2500)
                if hit_vpn_check(page):
                    raise CollectError("hh просит пройти проверку VPN")
                if "/account/login" in page.url:
                    raise CollectError("Нет сессии hh - нужен вход")
                rows = page.evaluate(
                    """() => [...document.querySelectorAll('a[href*="/vacancy/"]')].map(a => {
                        const card = a.closest('div[class], li, article') || a.parentElement;
                        return { href: a.getAttribute('href') || '',
                                 text: (card ? card.innerText : '').slice(0, 400) };
                    })"""
                )
                if not rows:
                    break
                for row in rows:
                    found = re.search(r"/vacancy/(\d+)", row.get("href") or "")
                    if not found:
                        continue
                    vacancy_id = found.group(1)
                    if vacancy_id in ours and vacancy_id not in seen:
                        seen[vacancy_id] = row.get("text") or ""
        finally:
            try:
                context.close()
            except Exception:
                pass

    updated = 0
    for vacancy_id, text in seen.items():
        outcome = classify_label(text)
        if ours[vacancy_id].get("response") != outcome:
            # Подпись кладём в журнал целиком: по ней видно, как читался исход,
            # если разбор подписей окажется неверным.
            store.set_response(vacancy_id, outcome, text.strip().splitlines()[0] if text.strip() else "")
            updated += 1
    log.info("исходы откликов: прочитано %s, обновлено %s", len(seen), updated)
    return {"read": len(seen), "updated": updated, "total_sent": len(ours)}


def _band(score: int | None) -> str:
    value = score or 0
    if value >= 90:
        return "90+"
    if value >= 80:
        return "80-89"
    if value >= 70:
        return "70-79"
    if value >= 60:
        return "60-69"
    return "ниже 60"


def _letter_band(length: int | None) -> str:
    value = length or 0
    if value >= 1300:
        return "длинное (1300+)"
    if value >= 1000:
        return "среднее (1000-1299)"
    return "короткое (до 1000)"


def report() -> dict:
    """Доля ответов по признакам, которые разведчик выбирал сам.

    Ответом считается всё, кроме молчания: и приглашение, и отказ. Отказ
    означает, что резюме открыли, а это уже про попадание в фильтр.
    """
    sent = [row for row in store.sent_applications() if row.get("response")]
    if not sent:
        return {"sent_with_outcome": 0, "note": "исходов пока нет"}

    cards = {str(find.get("id")): find for find in store.list_finds()}
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in sent:
        card = cards.get(row["vacancy_id"], {})
        outcome = row["response"]
        groups["балл"][_band(card.get("score"))].append(outcome)
        groups["мнение"][card.get("opinion") or "нет"].append(outcome)
        groups["трек"][card.get("track") or "нет"].append(outcome)
        groups["резюме"][row.get("resume") or "нет"].append(outcome)
        groups["длина письма"][_letter_band(row.get("letter_len"))].append(outcome)
        rating = card.get("employer_rating")
        groups["рейтинг работодателя"][
            "нет" if not rating else ("4 и выше" if float(rating) >= 4 else "ниже 4")
        ].append(outcome)

    def summarise(outcomes: list[str]) -> dict:
        total = len(outcomes)
        invited = sum(1 for o in outcomes if o == "invited")
        answered = sum(1 for o in outcomes if o != "silent")
        return {"откликов": total,
                "ответили": answered,
                "приглашений": invited,
                "доля ответов": round(100 * answered / total) if total else 0,
                "значимо": total >= MIN_GROUP}

    result = {name: {key: summarise(values) for key, values in sorted(buckets.items())}
              for name, buckets in groups.items()}
    overall = summarise([row["response"] for row in sent])
    return {"sent_with_outcome": len(sent), "итого": overall, "разрезы": result,
            "порог значимости": MIN_GROUP}


def advice() -> list[str]:
    """Что из отчёта стоит сделать с порогами. Только значимые группы."""
    data = report()
    if not data.get("sent_with_outcome"):
        return ["Исходов нет: отправленные отклики ещё не получили ответа."]

    base = data["итого"]["доля ответов"]
    lines = [f"Средняя доля ответов: {base}% на {data['итого']['откликов']} откликах."]
    for name, buckets in data["разрезы"].items():
        strong = {k: v for k, v in buckets.items() if v["значимо"]}
        if len(strong) < 2:
            continue
        best = max(strong.items(), key=lambda pair: pair[1]["доля ответов"])
        worst = min(strong.items(), key=lambda pair: pair[1]["доля ответов"])
        if best[1]["доля ответов"] - worst[1]["доля ответов"] < 15:
            continue
        lines.append(
            f"{name}: лучше всего «{best[0]}» ({best[1]['доля ответов']}% на "
            f"{best[1]['откликов']}), хуже всего «{worst[0]}» "
            f"({worst[1]['доля ответов']}% на {worst[1]['откликов']})."
        )
    if len(lines) == 1:
        lines.append(f"Значимых различий нет: в группах меньше {MIN_GROUP} откликов "
                     "либо разброс долей меньше 15 процентных пунктов.")
    return lines
