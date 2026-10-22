# -*- coding: utf-8 -*-
"""Отклик на вакансию: то же, что делает человек, только руками сервиса.

Последовательность повторяет действия Ивана: открыть вакансию, нажать
«Откликнуться», выбрать резюме, вставить сопроводительное письмо, ответить на
вопросы работодателя, нажать отправку.

Три предохранителя стоят до единого клика по отправке:

1. `dry_run` включён по умолчанию. Живая отправка требует явного False **и**
   настройки `live_apply=1` в базе - два разных выключателя, чтобы случайный
   вызов из кода ничего не отправил.
2. Карточка проходит через `application_plan.evaluate`. Любая причина
   блокировки - отклика нет.
3. Вопрос формы без заранее известного ответа останавливает отклик. Отвечать
   наугад за человека нельзя: ответ уходит работодателю от его имени.

Каждая попытка пишется в журнал `applications`, включая неудачные. Повторный
запуск по той же вакансии видит запись и второй отклик не отправляет.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from scout import store
from scout.application_plan import evaluate
from scout.collector import PROFILE, CollectError, VpnCheck, hit_vpn_check
from scout.question_plan import plan_answers

log = logging.getLogger(__name__)

SUBMIT_SELECTORS = (
    '[data-qa="vacancy-response-submit-popup"]',
    '[data-qa="vacancy-response-letter-submit"]',
    'button[data-qa*="response-submit"]',
)
LETTER_SELECTORS = (
    '[data-qa="vacancy-response-popup-form-letter-input"]',
    'textarea[data-qa*="letter"]',
    'textarea[name*="letter"]',
)


def live_allowed() -> bool:
    """Живая отправка включена отдельной настройкой, не кодом."""
    return store.get_setting("live_apply", "0") == "1"


def answers_for(find: dict, form: dict) -> tuple[list[dict], list[str]]:
    """Ответы на вопросы формы и список вопросов без ответа.

    Известные ответы берутся из политики: зарплата по резюме, ссылка на
    портфолио. Всё остальное требует человека.
    """
    planned = plan_answers(find, form)
    ready = [item for item in planned if item.get("status") == "suggested"]
    unknown = [item["question"] for item in planned if item.get("status") != "suggested"]
    return ready, unknown


def decide(find: dict, form: dict | None, now: datetime | None = None) -> dict:
    """Решение по одной вакансии до любого обращения к сети."""
    already = store.application(find.get("id", ""))
    if already and already.get("outcome") == "sent":
        return {"ok": False, "reason": "Отклик уже отправлен раньше", "answers": []}

    check = evaluate(find, form, now=now)
    if not check["eligible"]:
        return {"ok": False, "reason": "; ".join(check["reasons"]), "answers": []}

    ready, unknown = answers_for(find, form or {})
    if unknown:
        return {"ok": False, "answers": ready,
                "reason": "Вопросы без известного ответа: " + "; ".join(unknown[:3])}

    return {"ok": True, "reason": "", "answers": ready}


def _fill_form(page, letter: str, resume: str, answers: list[dict]) -> None:
    """Заполнить форму отклика: резюме, письмо, ответы на вопросы."""
    for selector in LETTER_SELECTORS:
        field = page.query_selector(selector)
        if field:
            field.fill(letter)
            break

    if resume:
        # Резюме выбирается радиокнопкой с подписью. Точного data-qa у hh нет,
        # поэтому ищем по тексту подписи рядом с переключателем.
        page.evaluate(
            """(name) => {
                const labels = [...document.querySelectorAll('label')];
                const hit = labels.find(l => (l.innerText || '').includes(name));
                if (hit) {
                    const input = hit.querySelector('input') ||
                        document.getElementById(hit.getAttribute('for') || '');
                    if (input && !input.checked) input.click();
                }
            }""",
            resume,
        )

    for answer in answers:
        text = answer.get("suggestion") or ""
        number = answer.get("number")
        if not text or not number:
            continue
        page.evaluate(
            """([index, value]) => {
                const areas = [...document.querySelectorAll('textarea, input[type="text"]')];
                const field = areas[index - 1];
                if (!field) return;
                const setter = Object.getOwnPropertyDescriptor(
                    field.constructor.prototype, 'value').set;
                setter.call(field, value);
                field.dispatchEvent(new Event('input', { bubbles: true }));
            }""",
            [number, text],
        )


def apply_one(vacancy_id: str, dry_run: bool = True, headless: bool = True) -> dict:
    """Один отклик. По умолчанию ничего не отправляет.

    Возвращает словарь с исходом и причиной. Запись в журнал делается в любом
    случае, в том числе когда отклик заблокирован.
    """
    from playwright.sync_api import sync_playwright
    from scout import response_form

    find = store.get_find(vacancy_id)
    if not find:
        return {"sent": False, "reason": "Вакансия не найдена в базе"}

    form = response_form.known(vacancy_id)
    verdict = decide(find, form)
    mode = "dry-run" if dry_run or not live_allowed() else "live"

    if not verdict["ok"]:
        store.log_application(vacancy_id, mode, "blocked", reason=verdict["reason"],
                              resume=find.get("resume"),
                              letter_len=len(find.get("letter") or ""))
        return {"sent": False, "reason": verdict["reason"], "mode": mode}

    if mode == "dry-run":
        store.log_application(vacancy_id, mode, "planned", reason="проверка без отправки",
                              resume=find.get("resume"),
                              letter_len=len(find.get("letter") or ""),
                              answers=json.dumps(verdict["answers"], ensure_ascii=False))
        return {"sent": False, "reason": "Режим проверки: отклик не отправлен",
                "mode": mode, "would_send": True}

    letter = find.get("letter") or ""
    resume = find.get("resume") or ""
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1380, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://hh.ru/applicant/resumes",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)
            if any(m in page.url for m in ("/account/login", "/account/signup")):
                raise CollectError("Нет сессии hh - нужен вход")

            page.goto(f"https://hh.ru/vacancy/{vacancy_id}",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)
            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN")

            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            if re.search(r"вы откликнулись|отклик отправлен", body, re.I):
                store.log_application(vacancy_id, mode, "sent",
                                      reason="отклик уже стоял на странице",
                                      resume=resume, letter_len=len(letter),
                                      sent_at=datetime.now().isoformat(timespec="seconds"))
                return {"sent": True, "reason": "Отклик уже был", "mode": mode}

            link = page.query_selector('[data-qa="vacancy-response-link-top"]')
            if not link:
                raise CollectError("Кнопка отклика не найдена - вакансия закрылась")
            link.click()
            page.wait_for_timeout(5000)
            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN на форме")

            _fill_form(page, letter, resume, verdict["answers"])
            page.wait_for_timeout(800)

            button = None
            for selector in SUBMIT_SELECTORS:
                button = page.query_selector(selector)
                if button:
                    break
            if not button:
                raise CollectError("Кнопка отправки не найдена")
            button.click()
            page.wait_for_timeout(4000)

            after = page.evaluate("() => document.body ? document.body.innerText : ''")
            sent = bool(re.search(r"отклик отправлен|резюме отправлено|вы откликнулись",
                                  after, re.I))
            now = datetime.now().isoformat(timespec="seconds")
            store.log_application(vacancy_id, mode, "sent" if sent else "failed",
                                  reason="" if sent else "подтверждение не найдено",
                                  resume=resume, letter_len=len(letter),
                                  answers=json.dumps(verdict["answers"], ensure_ascii=False),
                                  sent_at=now if sent else None)
            if sent:
                store.update_find(vacancy_id, status="sent", sent_at=now)
            return {"sent": sent, "mode": mode,
                    "reason": "" if sent else "hh не подтвердил отправку"}
        except Exception as problem:
            # В журнал идёт тип ошибки: текст может содержать кусок вакансии.
            store.log_application(vacancy_id, mode, "failed",
                                  reason=type(problem).__name__,
                                  resume=resume, letter_len=len(letter))
            raise
        finally:
            try:
                context.close()
            except Exception:
                pass


# Сколько откликов в сутки отправляет автоматический круг. hh смотрит на
# частоту: двести штук за час выглядят роботом даже при честном содержании.
# Живой человек в активном поиске делает десятки, поэтому держим тот же темп.
DAILY_LIMIT = 25

# Пауза между откликами внутри круга, секунды. Разброс намеренный.
PACE = (20, 55)


def ready_now(limit: int = DAILY_LIMIT) -> list[dict]:
    """Карточки, готовые к отправке прямо сейчас, в порядке убывания балла."""
    from scout import response_form
    from scout.application_plan import preview

    forms = response_form.all_forms()
    plan = preview(store.list_finds(), forms, limit=max(limit * 3, 30))
    ready = [item for item in plan if item["eligible"]]
    by_score = {str(f.get("id")): (f.get("score") or 0) for f in store.list_finds()}
    ready.sort(key=lambda item: -by_score.get(str(item["id"]), 0))
    return ready[:max(0, limit)]


def auto_round(send_photo=None, send_message=None, dry_run: bool = True) -> dict:
    """Один автоматический круг откликов.

    Порядок намеренный: сначала проверяем остаток суточного лимита, потом
    собираем готовых, и только потом трогаем сеть. Слетевшая сессия hh не
    отменяет круг - модуль просит человека войти через Telegram и продолжает
    с той же карточки.
    """
    import random
    import time

    from scout import hh_login

    live = live_allowed() and not dry_run
    left = DAILY_LIMIT - store.sent_today()
    if live and left <= 0:
        return {"mode": "live", "skipped": "суточный лимит исчерпан",
                "sent": 0, "ready": 0}

    ready = ready_now(left if live else DAILY_LIMIT)
    results: list[dict] = []
    sent = 0
    relogged = False
    position = 0
    while position < len(ready):
        item = ready[position]
        try:
            outcome = apply_one(item["id"], dry_run=not live)
        except CollectError as problem:
            # Сессия слетела посреди круга. Один раз зовём человека в бот и
            # повторяем ТУ ЖЕ карточку, не сдвигая позицию: иначе вакансия,
            # на которой сорвались, молча выпадает из круга.
            if relogged or "сесси" not in str(problem).lower() or send_photo is None:
                results.append({"id": item["id"], "sent": False,
                                "reason": type(problem).__name__})
                break
            relogged = True
            if send_message:
                send_message("Сессия hh слетела посреди откликов. Пришли ответ "
                             "для входа: цифры - код, другой текст - разгадка картинки.")
            done = hh_login.login(send_photo, send_message)
            if not done.get("ok"):
                results.append({"id": item["id"], "sent": False, "reason": "вход не удался"})
                break
            continue
        except VpnCheck:
            results.append({"id": item["id"], "sent": False, "reason": "проверка VPN"})
            break
        except Exception as problem:
            results.append({"id": item["id"], "sent": False,
                            "reason": type(problem).__name__})
            break
        results.append({"id": item["id"], **outcome})
        position += 1
        if outcome.get("sent"):
            sent += 1
            time.sleep(random.uniform(*PACE))
    return {"mode": "live" if live else "dry-run", "ready": len(ready),
            "sent": sent, "left_today": DAILY_LIMIT - store.sent_today(),
            "results": results}


def apply_batch(limit: int = 5, dry_run: bool = True) -> dict:
    """Пачка откликов по очереди готовности. Лимит намеренно маленький."""
    from scout import response_form
    from scout.application_plan import preview

    forms = response_form.all_forms()
    plan = preview(store.list_finds(), forms)
    ready = [item for item in plan if item["eligible"]][:max(0, min(limit, 20))]
    results = []
    for item in ready:
        try:
            results.append({"id": item["id"], **apply_one(item["id"], dry_run=dry_run)})
        except Exception as problem:
            results.append({"id": item["id"], "sent": False,
                            "reason": type(problem).__name__})
            break                                   # одна поломка - дальше те же
    return {"mode": "dry-run" if dry_run or not live_allowed() else "live",
            "ready": len(ready), "results": results}
