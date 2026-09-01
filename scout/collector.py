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

import re
from pathlib import Path

PROFILE = Path(__file__).resolve().parent.parent / ".browser"

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
    const money = [...txt.matchAll(/(\d[\d\s]{4,})\s*₽/g)].map(m => parseInt(m[1].replace(/\s/g, "")));
    const emp = txt.match(/(?:Опыт[^А-Я]*|Без опыта\s*)(?:Можно удалённо\s*)?([А-ЯA-Za-z][^•]{1,40})/);
    items.push({
      id,
      name: a.textContent.trim(),
      url: "https://hh.ru/vacancy/" + id,
      employer: emp ? emp[1].trim() : "",
      salary_from: money.length ? Math.min(...money) : null,
      salary_to: money.length ? Math.max(...money) : null,
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


def collect(search_url: str, pages: int = 2, headless: bool = True) -> list[dict]:
    """Открывает выдачу и снимает карточки. Возвращает список находок."""
    if not search_url.strip():
        raise CollectError("Не задана ссылка на выдачу hh - укажите её в настройках.")

    from playwright.sync_api import sync_playwright

    found: list[dict] = []
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for number in range(pages):
                url = search_url
                if number:
                    url = re.sub(r"([?&])page=\d+", "", url)
                    url += ("&" if "?" in url else "?") + f"page={number}"
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                if response and response.status >= 400:
                    raise CollectError(
                        f"hh ответил {response.status}. Проверьте VPN и вход в аккаунт."
                    )
                page.wait_for_timeout(1500)
                chunk = page.evaluate(EXTRACT)
                if not chunk:
                    break
                found.extend(chunk)
        except CollectError:
            raise
        except Exception as error:
            raise CollectError(f"Не удалось собрать выдачу: {error}") from error
        finally:
            context.close()

    unique: dict[str, dict] = {}
    for item in found:
        unique.setdefault(item["id"], item)
    return list(unique.values())


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
            page.wait_for_timeout(1200)
            return "/account/login" not in page.url
        except Exception:
            return False
        finally:
            context.close()


def fetch_descriptions(vacancy_ids: list[str], headless: bool = True) -> dict[str, str]:
    """Читает описания пачкой в одном браузере.

    Профиль браузера лежит на диске и блокируется при открытии, поэтому
    поднимать по браузеру на вакансию нельзя - они мешают друг другу
    и описания приходят пустыми.
    """
    from playwright.sync_api import sync_playwright

    result: dict[str, str] = {}
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
                            "hh показывает проверку VPN и не отдаёт описания вакансий. "
                            "Откройте браузер Скаута кнопкой «Войти в hh» и пройдите проверку "
                            "вручную - после этого сессия запомнится."
                        )
                    page.wait_for_selector('[data-qa="vacancy-description"]', timeout=15_000)
                    result[vacancy_id] = page.evaluate(
                        "() => document.querySelector('[data-qa=\"vacancy-description\"]')?.innerText || ''"
                    )
                except VpnCheck:
                    raise                        # это про всю пачку, а не про одну вакансию
                except Exception:
                    result[vacancy_id] = ""      # одна недоступная не должна рвать пачку
                page.wait_for_timeout(400)       # вежливость к чужому сайту
        finally:
            context.close()
    return result
