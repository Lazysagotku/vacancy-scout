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

  // Поле сопроводительного письма. На части вакансий оно появляется только
  // после нажатия «Добавить сопроводительное», поэтому наличие такой кнопки
  // считается тем же самым: заполнитель формы её нажмёт перед вводом.
  const letterField = document.querySelector(
    'textarea[data-qa*="letter"], textarea[name*="letter"], [data-qa*="letter"], ' +
    '[contenteditable="true"], [data-qa="vacancy-response-popup-form-letter-input"]');
  // Кнопка может быть любым элементом: hh рисует её и ссылкой, и span, и div.
  const letterToggle = [...document.querySelectorAll("*")]
    .find(el => el.children.length === 0 && /сопроводительн/i.test(el.textContent || ""));
  out.hasLetter = !!(letterField || letterToggle);

  // Снимок того, из чего форма состоит. Нужен, чтобы причина отказа читалась
  // по журналу, а не выяснялась отдельным заходом на живую вакансию: нажатие
  // «Откликнуться» на части вакансий отправляет отклик сразу.
  out.letterProbe = {
    textareas: [...document.querySelectorAll("textarea")]
      .map(t => t.getAttribute("data-qa") || t.getAttribute("name") || "без имени").slice(0, 6),
    editable: document.querySelectorAll('[contenteditable="true"]').length,
    sawWord: !!letterToggle,
    qa: [...new Set([...document.querySelectorAll("[data-qa]")]
      .map(e => e.getAttribute("data-qa"))
      .filter(v => /letter|popup|submit/i.test(v)))].slice(0, 12),
  };

  // Кандидаты в контейнеры вопросов
  const blocks = [...document.querySelectorAll(
    '[data-qa*="task"], [data-qa*="question"], fieldset, [class*="question"], [class*="task"]'
  )];

  // Виджет самого hh: список готовых вопросов, которые кандидат может задать
  // работодателю. Это предложение, а не требование, и ответа оно не ждёт.
  // Без этого отсева он блокировал отклик как «вопрос без известного ответа».
  const OWN_WIDGET = /задайте вопрос работодателю|задать вопрос работодателю/i;
  // Надёжнее текста: у виджета hh свой data-qa, проверено на живой вакансии.
  const ownQa = (el) => /vacancy-response-question/i.test(el.getAttribute("data-qa") || "");

  const seen = new Set();
  for (const b of blocks) {
    const text = clean(b.innerText);
    if (!text || text.length < 8 || seen.has(text)) continue;
    if (OWN_WIDGET.test(text) || ownQa(b) || b.closest("[data-qa*='vacancy-response-question']")) continue;

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
            # Прогрев сессии. Без него hh отдаёт страницу вакансии как гостю:
            # куки в профиле есть, но применяются только после захода в раздел
            # соискателя. Это же объясняет, почему кнопка входа в интерфейсе
            # подтверждает авторизацию не сразу, а через несколько секунд.
            page.goto("https://hh.ru/applicant/resumes",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)
            if any(m in page.url for m in ("/account/login", "/account/signup")):
                raise CollectError("Нет сессии hh. Нажмите «Войти в hh» и войдите в аккаунт.")

            page.goto(f"https://hh.ru/vacancy/{vacancy_id}",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)
            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN")

            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            if re.search(r"вы откликнулись|отклик отправлен", body, re.I):
                result["note"] = "На эту вакансию уже был отклик"
                return _remember(result)

            link = page.query_selector('[data-qa="vacancy-response-link-top"]')
            if not link:
                result["note"] = "Кнопка отклика не найдена: вакансия могла закрыться"
                return _remember(result)

            # Кликаем, а не переходим по href: прямой переход теряет контекст
            # перехода, и hh подсовывает форму регистрации вместо отклика.
            # Первое нажатие отклик НЕ отправляет - оно открывает форму,
            # где выбирают резюме и пишут письмо. Проверено на живой вакансии.
            link.click()
            page.wait_for_timeout(5000)

            if any(m in page.url for m in ("/account/login", "/account/signup")):
                raise CollectError("hh показал форму регистрации вместо отклика: нужен вход.")

            # Адрес при этом не меняется: hh рисует регистрацию прямо внутри
            # окна отклика. Без этой проверки разведка записывала «поля письма
            # нет» вместо «нет сессии», и карточка навсегда оставалась
            # заблокированной по ложной причине. Найдено 23.10 по снимку
            # разметки: единственным управляющим элементом окна был
            # account-signup-submit.
            if page.query_selector('[data-qa*="account-signup"], [data-qa*="account-login"]'):
                raise CollectError("hh показал регистрацию внутри формы отклика: нужен вход.")
            if hit_vpn_check(page):
                raise VpnCheck("hh показывает проверку VPN на форме отклика")

            # Страховка: если hh всё же отправил отклик, это надо увидеть сразу
            after = page.evaluate("() => document.body ? document.body.innerText : ''")
            if re.search(r"отклик отправлен|резюме отправлено", after, re.I):
                result["note"] = "ВНИМАНИЕ: hh отправил отклик сам, без второго шага"
                result["url"] = page.url
                return _remember(result)

            data = page.evaluate(EXTRACT)
            result["questions"] = data.get("questions") or []
            result["has_letter"] = bool(data.get("hasLetter"))
            result["letter_probe"] = data.get("letterProbe") or {}
            result["resumes"] = data.get("resumes") or []
            result["url"] = page.url
            # hh сам говорит в адресе, начиналась ли форма с вопросов
            result["has_questions_flag"] = "startedWithQuestion=true" in page.url
            if not result["questions"]:
                result["note"] = ("Дополнительных вопросов нет: только выбор резюме "
                                  "и сопроводительное письмо")
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
