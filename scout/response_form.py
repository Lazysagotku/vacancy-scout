# -*- coding: utf-8 -*-
"""Разведка формы отклика: какие вопросы задаст работодатель.

Зачем это нужно. У части вакансий hh после нажатия «Откликнуться» показывает
дополнительные вопросы от работодателя. Узнать о них заранее нельзя: в
описании вакансии их нет. А написать сопроводительное письмо до того, как
увидел вопросы, значит написать его дважды - потому что в некоторых вопросах
есть вариант «ответ в сопроводительном письме», и тогда письмо строится иначе.

Что делает модуль: открывает форму отклика, снимает с неё вопросы и варианты
ответов, закрывает страницу. **Отклик не отправляется.** Кнопка отправки не
нажимается ни при каких условиях - см. guard ниже.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from scout.collector import PROFILE, CollectError, VpnCheck, hit_vpn_check

STORE = Path(__file__).resolve().parent.parent / "response_forms.json"

# Слова, по которым узнаём кнопку отправки. Нужны, чтобы её ОБХОДИТЬ:
# скрипт ходит по форме и не должен случайно кликнуть отправку.
SUBMIT_WORDS = ("отклик", "отправить", "подтвердить", "продолжить")


EXTRACT = r"""
() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  // Вопросы работодателя на hh живут в блоке с задачами-вопросами.
  // Разметка меняется, поэтому берём широко: любой блок, где есть текст
  // вопроса и рядом поля ввода или варианты.
  const out = { questions: [], resumes: [], hasLetter: false, raw: "" };

  // Резюме на выбор
  out.resumes = [...document.querySelectorAll('[data-qa*="resume"] , [name="resume_hash"]')]
    .map(el => clean(el.innerText || el.value)).filter(Boolean).slice(0, 10);

  // Поле сопроводительного письма
  out.hasLetter = !!document.querySelector('textarea[data-qa*="letter"], textarea[name*="letter"], [data-qa="vacancy-response-popup-form-letter-input"]');

  // Кандидаты в контейнеры вопросов
  const blocks = [...document.querySelectorAll(
    '[data-qa*="task"], [data-qa*="question"], fieldset, [class*="question"], [class*="task"]'
  )];

  const seen = new Set();
  for (const b of blocks) {
    const text = clean(b.innerText);
    if (!text || text.length < 8 || seen.has(text)) continue;

    const radios = [...b.querySelectorAll('input[type="radio"]')];
    const checks = [...b.querySelectorAll('input[type="checkbox"]')];
    const areas  = [...b.querySelectorAll('textarea')];
    const texts  = [...b.querySelectorAll('input[type="text"], input:not([type])')];

    if (!radios.length && !checks.length && !areas.length && !texts.length) continue;

    const labelOf = (input) => {
      const id = input.id;
      let l = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
      if (!l) l = input.closest("label");
      return clean(l ? l.innerText : input.value);
    };

    // Заголовок вопроса: первый заметный текст блока до вариантов
    const heading = clean(
      b.querySelector("legend, h1, h2, h3, h4, p, span")?.innerText || text
    ).slice(0, 400);

    const options = [...radios, ...checks].map(labelOf).filter(Boolean);

    out.questions.push({
      question: heading,
      kind: checks.length ? "checkbox" : radios.length ? "radio" : areas.length ? "text" : "input",
      options: [...new Set(options)].slice(0, 20),
      required: /\*/.test(text) || b.getAttribute("aria-required") === "true",
    });
    seen.add(text);
  }

  out.raw = clean(document.body.innerText).slice(0, 4000);
  return out;
}
"""


def _load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def peek(vacancy_id: str, headless: bool = True) -> dict:
    """Открывает форму отклика и снимает вопросы. Отклик не отправляет.

    Возвращает словарь с вопросами, вариантами ответов и признаком того,
    есть ли поле сопроводительного письма.
    """
    from playwright.sync_api import sync_playwright

    result = {
        "id": vacancy_id, "checked_at": datetime.now().isoformat(timespec="seconds"),
        "questions": [], "has_letter": False, "resumes": [], "note": "",
    }

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1380, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(f"https://hh.ru/vacancy/{vacancy_id}",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(1800)
            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN")

            # Признак входа тот же, что у остального скаута: неавторизованного
            # hh уводит на страницу логина. Меню профиля на карточке вакансии
            # отсутствует даже при живой сессии, по нему проверять нельзя.
            if any(m in page.url for m in ("/account/login", "/account/signup")):
                raise CollectError("Нет сессии hh. Нажмите «Войти в hh» и войдите в аккаунт.")

            body = page.evaluate("() => document.body.innerText")
            if re.search(r"вы откликнулись|отклик отправлен", body, re.I):
                result["note"] = "На эту вакансию уже был отклик"
                return _remember(result)

            link = page.query_selector('[data-qa="vacancy-response-link-top"]')
            if not link:
                result["note"] = "Кнопка отклика не найдена: вакансия могла закрыться"
                return _remember(result)

            # Переходим по ссылке формы, не кликая: клик может открыть
            # всплывающее окно с уже нажатой отправкой в некоторых сценариях
            href = link.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://hh.ru" + href
            page.goto(href, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)

            # Форма отклика для неавторизованного превращается в регистрацию:
            # это самый надёжный признак, что сессии нет.
            if any(m in page.url for m in ("/account/login", "/account/signup")):
                raise CollectError("hh показал форму регистрации вместо отклика: нужен вход в аккаунт.")

            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN на форме отклика")

            data = page.evaluate(EXTRACT)
            result["questions"] = data.get("questions") or []
            result["has_letter"] = bool(data.get("hasLetter"))
            result["resumes"] = data.get("resumes") or []
            result["url"] = page.url
            if not result["questions"]:
                result["note"] = "Дополнительных вопросов нет, только резюме и письмо"
        finally:
            # Форму закрываем, ничего не отправив
            try:
                context.close()
            except Exception:
                pass

    return _remember(result)


def _remember(result: dict) -> dict:
    data = _load()
    data[result["id"]] = result
    _save(data)
    return result


def known(vacancy_id: str) -> dict | None:
    return _load().get(vacancy_id)


def all_forms() -> dict:
    return _load()
