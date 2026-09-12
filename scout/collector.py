# -*- coding: utf-8 -*-
"""Сбор вакансий с выдачи hh через локальный браузер.

API hh для соискателей закрыт, поэтому идём тем же путём, что и человек:
открываем страницу выдачи и читаем карточки. Отсюда два следствия.

Первое: нужен включённый VPN - без него hh не отвечает. Если сбор упал,
это первое, что стоит проверить.

Второе: браузер поднимается со своим профилем и сессия hh в нём сохраняется
между запусками. Первый раз может потребоваться войти вручную - для этого
есть режим headless=False.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

log = logging.getLogger(__name__)

PROFILE = Path(__file__).resolve().parent.parent / ".browser"

MAX_PAGES = 40          # выдача hh редко длиннее 30 страниц, дальше она повторяет последнюю
PAGE_SIZE = 100         # items_on_page: вдвое меньше загрузок, чем при 50 по умолчанию


def pause(page, low: float = 1.5, high: float = 3.0) -> None:
    """Пауза между страницами, как у человека.

    Браузер залогинен под аккаунтом Ивана, и с него же уходят отклики.
    Сотни страниц без пауз - это капча или блокировка именно этого аккаунта,
    поэтому экономить здесь нельзя.
    """
    page.wait_for_timeout(int(random.uniform(low, high) * 1000))

# Логика разбора карточки живёт в браузере: так проще пережить смену вёрстки -
# правится один кусок, и он же отлаживается в консоли руками.
EXTRACT = r"""
() => {
  const seen = new Set(), items = [];
  document.querySelectorAll('a[href*="/vacancy/"]').forEach(a => {
    const id = (a.href.match(/vacancy\/(\d+)/) || [])[1];
    if (!id || seen.has(id) || !a.textContent.trim()) return;
    seen.add(id);
    let card = a;
    for (let i = 0; i < 8 && card.parentElement; i++) {
      card = card.parentElement;
      if (card.textContent.length > 200) break;
    }
    const txt = (card.innerText || "").replace(/\s+/g, " ").trim();
    // Предлог решает всё: «от 150 000» и «до 150 000» - разные вилки, но
    // цифра одна. Раньше брались просто min и max по всем суммам, и
    // одиночное «от 150 000» превращалось в «до 150к».
    const num = (s) => parseInt(s.replace(/\s/g, ""));
    const mFrom = txt.match(/от\s*(\d[\d\s]{4,})\s*₽/i);
    const mTo   = txt.match(/до\s*(\d[\d\s]{4,})\s*₽/i);
    const mRange = txt.match(/(\d[\d\s]{4,})\s*[–—-]\s*(\d[\d\s]{4,})\s*₽/);
    const money = [...txt.matchAll(/(\d[\d\s]{4,})\s*₽/g)].map(m => num(m[1]));

    let payFrom = null, payTo = null;
    if (mRange) {                    // «150 000 – 200 000 ₽»
      payFrom = num(mRange[1]);
      payTo = num(mRange[2]);
    } else if (mFrom || mTo) {       // «от 150 000 ₽» либо «до 200 000 ₽»
      payFrom = mFrom ? num(mFrom[1]) : null;
      payTo = mTo ? num(mTo[1]) : null;
    } else if (money.length) {       // «150 000 ₽» без предлога - точная сумма
      payFrom = Math.min(...money);
      payTo = Math.max(...money);
    }
    const emp = txt.match(/(?:Опыт[^А-Я]*|Без опыта\s*)(?:Можно удалённо\s*)?([А-ЯA-Za-z][^•]{1,40})/);
    items.push({
      id,
      name: a.textContent.trim(),
      url: "https://hh.ru/vacancy/" + id,
      employer: emp ? emp[1].trim() : "",
      salary_from: payFrom,
      salary_to: payTo,
      experience: (txt.match(/Без опыта|Опыт \d[^А-Я]*/) || [""])[0].trim(),
      remote: /Можно удалённо/.test(txt),
    });
  });
  return items;
}
"""


class CollectError(RuntimeError):
    """Сбор не удался - чаще всего выключен VPN или слетела сессия."""


def hit_vpn_check(page) -> bool:
    """Не упёрлись ли в заглушку hh про VPN.

    При включённом VPN hh перекидывает карточку вакансии на /vpncheeck.
    Там две кнопки: повторить и «я не использую VPN». Вторую жать нельзя -
    это заявление сервису, которое не соответствует действительности,
    поэтому проверку не обходим, а честно сообщаем наверх.
    """
    return "vpncheeck" in page.url


class VpnCheck(CollectError):
    """hh требует пройти проверку VPN - нужен человек."""


def _page_url(search_url: str, number: int) -> str:
    """Ссылка на страницу выдачи с крупным шагом. Главная hh параметров не принимает."""
    url = re.sub(r"([?&])page=\d+", "", search_url)
    url = re.sub(r"([?&])items_on_page=\d+", "", url)
    if "/search/" not in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}items_on_page={PAGE_SIZE}&page={number}"


def collect(search_url: str, pages: int = MAX_PAGES, headless: bool = True,
            known: set[str] | None = None) -> list[dict]:
    """Открывает выдачу и снимает карточки до конца. Возвращает список находок.

    Идём по страницам, пока они не кончатся. Ранний стоп: выдача отсортирована
    по дате, и если целая страница состоит из уже известных id - дальше только
    старое. Так ночной проход берёт всё, а дневной обходится одной страницей.
    """
    if not search_url.strip():
        raise CollectError("Не задана ссылка на выдачу hh - укажите её в настройках.")
    if not search_url.startswith("http"):
        raise CollectError("Ссылка не начинается с http - проверь строку в настройках.")

    from playwright.sync_api import sync_playwright

    known = known or set()
    found: list[dict] = []
    seen: set[str] = set()
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for number in range(pages):
                response = page.goto(_page_url(search_url, number),
                                     wait_until="domcontentloaded", timeout=45_000)
                if response and response.status >= 400:
                    raise CollectError(f"hh ответил {response.status} - проверь вход в аккаунт.")
                if hit_vpn_check(page):
                    raise VpnCheck("hh просит пройти проверку VPN - открой браузер кнопкой «Войти в hh».")
                page.wait_for_timeout(1500)
                chunk = page.evaluate(EXTRACT)
                fresh = [c for c in chunk if c["id"] not in seen]
                if not fresh:
                    break                                    # выдача кончилась или пошла по кругу
                seen.update(c["id"] for c in fresh)
                found.extend(fresh)
                if all(c["id"] in known for c in fresh):
                    break                                    # дальше только известное
                if "/search/" not in search_url:
                    break                                    # главная - одна страница
                pause(page)
        except CollectError:
            raise
        except Exception as error:
            raise CollectError(short_error(error)) from error
        finally:
            context.close()
    return found


def short_error(error: Exception) -> str:
    """Короткая причина вместо простыни Playwright.

    Иван 12.09: «длинные строки пугают и как будто всё сломалось». Полный
    текст уходит в лог, человеку - одна строка с тем, что делать.
    """
    text = str(error)
    log.warning("сборщик: %s", text[:2000])
    checks = (
        ("invalid URL", "Ссылка не открывается - проверь, что каждая ссылка на своей строке."),
        ("vpncheeck", "hh просит пройти проверку VPN - открой браузер кнопкой «Войти в hh»."),
        ("Timeout", "hh не ответил за 45 секунд - выключен VPN или лежит сеть."),
        ("net::ERR_", "Нет сети до hh - выключен VPN или лежит сеть."),
        ("ProcessSingleton", "Браузер разведчика уже открыт другим процессом - подожди минуту."),
        ("Target page, context or browser has been closed", "Окно браузера закрылось во время сбора."),
    )
    for needle, reason in checks:
        if needle in text:
            return reason
    first = text.strip().splitlines()[0] if text.strip() else "неизвестная ошибка"
    return first[:140]


def fetch_description(vacancy_id: str, headless: bool = True) -> str:
    """Догружает полное описание одной вакансии - нужно для разбора."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(f"https://hh.ru/vacancy/{vacancy_id}",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(1200)
            return page.evaluate(
                "() => document.querySelector('[data-qa=\"vacancy-description\"]')?.innerText || ''"
            )
        except Exception as error:
            raise CollectError(f"Не удалось прочитать вакансию {vacancy_id}: {error}") from error
        finally:
            context.close()


def open_login(wait_minutes: int = 10) -> None:
    """Открывает видимый браузер на hh, чтобы войти в аккаунт один раз.

    Ссылка на выдачу по резюме работает только у авторизованного пользователя:
    без сессии hh отдаёт общий поиск, и в списке появляются вакансии
    вроде «ведущий свадебной церемонии». Профиль браузера сохраняется на диске,
    поэтому вход нужен один раз, а не перед каждым сбором.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://hh.ru/", wait_until="domcontentloaded", timeout=45_000)
        deadline = wait_minutes * 60 * 1000
        step = 3000
        waited = 0
        while waited < deadline:
            try:
                if not context.pages:
                    break                       # окно закрыли - значит вошли
                page.wait_for_timeout(step)
                waited += step
                if page.evaluate("() => !!document.querySelector('[data-qa=\"mainmenu_applicantProfile\"]')"):
                    break                       # появилось меню профиля - вход есть
            except Exception:
                break
        try:
            context.close()
        except Exception:
            pass


def is_authorized() -> bool:
    """Проверяет, есть ли живая сессия hh в профиле Скаута."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=True, viewport={"width": 1280, "height": 900})
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://hh.ru/applicant/resumes", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1500)
            # Куки проверяем до разметки: они не зависят от вёрстки hh,
            # которая меняется и уносит с собой data-qa атрибуты.
            names = {c["name"] for c in context.cookies() if "hh.ru" in c.get("domain", "")}
            if "hhtoken" not in names or "hhrole" not in names:
                return False
            # По URL судить нельзя: неавторизованного hh уводит и на /account/login,
            # и на /account/signup, а иногда отдаёт страницу резюме с формой входа
            # внутри. Поэтому смотрим на признак живой сессии в самой странице.
            if any(m in page.url for m in ("/account/login", "/account/signup", "/auth")):
                return False
            # Разметку профиля hh переделывал: селекторы вроде mainmenu_applicantProfile
            # больше не находятся даже при живой сессии. Надёжнее смотреть на то,
            # что видно человеку: кнопки входа нет, а разделы соискателя есть.
            return page.evaluate(
                "() => !document.querySelector('[data-qa=\"login\"]')"
                " && /Отклики|Резюме и профиль|Мои резюме/.test(document.body.innerText)"
            )
        except Exception:
            return False
        finally:
            context.close()


# Страница вакансии целиком: шапка, компания, описание, навыки, архив.
# Селекторы data-qa сняты с живой страницы 12.09; на случай их смены есть
# запасной разбор по тексту страницы - подписи «График:», «Оформление:»
# видны человеку и меняются реже, чем атрибуты.
DETAILS = r"""
() => {
  const text = (sel) => (document.querySelector(sel)?.innerText || "").replace(/\s+/g, " ").trim();
  const body = document.body.innerText || "";
  const byLabel = (label) => {
    const m = body.match(new RegExp(label + ":\s*([^\n]+)"));
    return m ? m[1].trim() : "";
  };
  let skills = [...document.querySelectorAll('[data-qa="skills-element"]')]
      .map(e => e.innerText.trim()).filter(Boolean);
  if (!skills.length) {
    const i = body.indexOf("Ключевые навыки");
    if (i >= 0) {
      const chunk = body.slice(i + 15, i + 1500);
      const stop = chunk.search(/Соискателям с особенностями|Контакты|Вакансия опубликована|Похожие вакансии|Задайте вопрос|Откликнуться/);
      skills = (stop >= 0 ? chunk.slice(0, stop) : chunk).split("\n")
        .map(x => x.trim()).filter(x => x && x.length < 60);
    }
  }
  const archived = !!document.querySelector('[data-qa="vacancy-title-archived-text"], [data-qa="vacancy-archive-description"]');
  return {
    title: text('[data-qa="vacancy-title"]'),
    experience: text('[data-qa="vacancy-experience"]') || byLabel("Опыт работы"),
    employment: text('[data-qa="common-employment-text"]'),
    hiring: text('[data-qa="vacancy-hiring-formats"]') || byLabel("Оформление"),
    schedule: text('[data-qa="work-schedule-by-days-text"]') || byLabel("График"),
    hours: text('[data-qa="working-hours-text"]') || byLabel("Рабочие часы"),
    work_format: text('[data-qa="work-formats-text"]') || byLabel("Формат работы"),
    employer: text('[data-qa="vacancy-company-name"]'),
    rating: text('[data-qa="employer-review-small-widget-total-rating"]'),
    reviews: text('[data-qa="employer-review-small-widget-review-count-action"]'),
    skills,
    archived,
    archived_text: text('[data-qa="vacancy-title-archived-text"]'),
    description: document.querySelector('[data-qa="vacancy-description"]')?.innerText || "",
  };
}
"""

LABELS = re.compile(r"^(Опыт работы|Оформление|График|Рабочие часы|Формат работы)\s*:\s*", re.I)


def _tidy(raw: dict) -> dict:
    """Приводит снятое со страницы к тому, что кладётся в базу."""
    def clean(key: str) -> str:
        return LABELS.sub("", (raw.get(key) or "").replace(" ", " ")).strip()

    rating = None
    m = re.search(r"\d[.,]\d", raw.get("rating") or "")
    if m:
        rating = float(m.group().replace(",", "."))
    reviews = None
    m = re.search(r"(\d[\d\s\u202f\u00a0]*)\s*отзыв", raw.get("reviews") or "")
    if m:
        reviews = int(re.sub(r"\D", "", m.group(1)))
    archived_at = ""
    if raw.get("archived"):
        m = re.search(r"с\s+(\d{1,2}\s+\S+(?:\s+\d{4})?)", raw.get("archived_text") or "")
        archived_at = m.group(1) if m else "да"
    title = (raw.get("title") or "").strip()
    if raw.get("archived_text"):
        title = title.replace(raw["archived_text"].strip(), "").strip()
    return {
        "title": title,
        "experience": clean("experience"),
        "employment": clean("employment"),
        "hiring": clean("hiring"),
        "schedule": clean("schedule"),
        "hours": clean("hours"),
        "work_format": clean("work_format"),
        "employer": (raw.get("employer") or "").strip(),
        "employer_rating": rating,
        "employer_reviews": reviews,
        "key_skills": ", ".join(dict.fromkeys(raw.get("skills") or [])),
        "archived_at": archived_at,
        "description": (raw.get("description") or "").strip(),
    }


def fetch_details(vacancy_ids: list[str], headless: bool = True,
                  on_progress=None) -> dict[str, dict]:
    """Читает страницы вакансий пачкой в одном браузере.

    Профиль браузера лежит на диске и блокируется при открытии, поэтому
    поднимать по браузеру на вакансию нельзя - они мешают друг другу
    и описания приходят пустыми.

    На выходе по каждому id словарь из _tidy. Пустое описание у живой
    вакансии - страница не отдалась; у архивной - archived_at заполнен,
    и описание там не нужно.
    """
    from playwright.sync_api import sync_playwright

    result: dict[str, dict] = {}
    if not vacancy_ids:
        return result

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for vacancy_id in vacancy_ids:
                try:
                    page.goto(f"https://hh.ru/vacancy/{vacancy_id}",
                              wait_until="domcontentloaded", timeout=45_000)
                    if hit_vpn_check(page):
                        raise VpnCheck(
                            "hh просит пройти проверку VPN и не отдаёт вакансии - "
                            "открой браузер кнопкой «Войти в hh» и пройди проверку."
                        )
                    try:
                        page.wait_for_selector(
                            '[data-qa="vacancy-description"], [data-qa="vacancy-archive-description"]',
                            timeout=15_000)
                    except Exception:
                        pass                     # снимем, что есть: шапка часто уже на месте
                    # Виджет рейтинга дорисовывается на полсекунды позже описания
                    try:
                        page.wait_for_selector('[data-qa="employer-review-small-widget-total-rating"]',
                                               timeout=2_500)
                    except Exception:
                        pass                     # у компании без отзывов виджета нет
                    result[vacancy_id] = _tidy(page.evaluate(DETAILS))
                except VpnCheck:
                    raise                        # это про всю пачку, а не про одну вакансию
                except Exception as error:
                    log.warning("вакансия %s не прочиталась: %s", vacancy_id, str(error)[:300])
                    result[vacancy_id] = _tidy({})   # одна недоступная не должна рвать пачку
                if on_progress:
                    on_progress(len(result), len(vacancy_ids))
                pause(page)
        finally:
            context.close()
    return result


def fetch_descriptions(vacancy_ids: list[str], headless: bool = True,
                       on_progress=None) -> dict[str, str]:
    """Только описания - для старых вызовов."""
    return {k: v["description"] for k, v in fetch_details(vacancy_ids, headless, on_progress).items()}
