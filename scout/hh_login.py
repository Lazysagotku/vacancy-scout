# -*- coding: utf-8 -*-
"""Вход в hh с помощью человека через Telegram.

Сессия hh в профиле браузера живёт долго, но не вечно. Когда она слетает, сбор
останавливается до тех пор, пока Иван не сядет за компьютер. Этот модуль убирает
ожидание: сервис открывает страницу входа, присылает в личный чат снимок экрана
и ждёт ответ.

Как различается ответ, по просьбе Ивана: **ответ из одних цифр - это код
подтверждения, любой другой текст - разгадка картинки с проверкой.** После
текста ожидание продолжается, потому что следом hh пришлёт код.

Капчу сервис не разгадывает: картинку читает человек, сервис только передаёт
снимок и вводит присланное. Автоматического обхода проверок здесь нет.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

CODE = re.compile(r"^\s*(\d[\d\s-]{3,9})\s*$")
WAIT_SECONDS = 300


def classify(answer: str) -> str:
    """Что прислал человек: код подтверждения или разгадку картинки."""
    return "code" if CODE.match(answer or "") else "text"


def digits(answer: str) -> str:
    return re.sub(r"\D", "", answer or "")


@dataclass
class Pending:
    """Ожидание ответа человека на одном шаге входа."""

    stage: str                                  # captcha | code
    asked_at: datetime
    event: threading.Event = field(default_factory=threading.Event)
    answer: str = ""

    def resolve(self, answer: str) -> None:
        self.answer = answer
        self.event.set()

    def wait(self, seconds: int = WAIT_SECONDS) -> str | None:
        return self.answer if self.event.wait(seconds) else None


_pending: Pending | None = None
_lock = threading.Lock()


def awaiting() -> Pending | None:
    with _lock:
        return _pending


def ask(stage: str) -> Pending:
    """Начать ожидание ответа. Прошлое ожидание отменяется."""
    global _pending
    with _lock:
        _pending = Pending(stage=stage, asked_at=datetime.now())
        return _pending


def deliver(answer: str) -> dict:
    """Передать ответ человека в ожидающий шаг входа.

    Вызывается обработчиком Telegram. Возвращает, что с ответом сделали, чтобы
    бот ответил человеку понятной строкой.
    """
    global _pending
    with _lock:
        waiting = _pending
    if waiting is None:
        return {"taken": False, "note": "Сейчас вход не запрашивается"}

    kind = classify(answer)
    if waiting.stage == "code" and kind != "code":
        return {"taken": False, "note": "Жду код из цифр"}

    waiting.resolve(answer)
    with _lock:
        _pending = None
    return {"taken": True, "kind": kind,
            "note": "Код принят" if kind == "code" else "Ответ принят, жду код"}


def clear() -> None:
    global _pending
    with _lock:
        _pending = None


# Кнопки, которые двигают вход дальше и ничего не спрашивают у человека.
# «Зарегистрироваться» сюда не входит намеренно: аккаунт у Ивана уже есть.
ADVANCE = r"^(войти|продолжить|далее|подтвердить|получить код|выслать код|войти с паролем)$"
SKIP = r"регистр|создать аккаунт|работодател|сотрудник"

# Сколько раз подряд сценарий может нажать кнопку сам, не спрашивая человека.
MAX_ADVANCES = 4


def _first_input(page):
    """Первое видимое незаполненное поле ввода или None."""
    return page.evaluate_handle(
        """() => {
            const fields = [...document.querySelectorAll(
                'input:not([type=hidden]):not([type=checkbox]):not([type=radio])')]
                .filter(i => !i.disabled && !i.readOnly && i.offsetParent !== null);
            return fields.find(i => !i.value) || fields[0] || null;
        }"""
    ).as_element()


def _advance(page, advance: str, skip: str) -> str:
    """Нажать кнопку, которая двигает вход дальше. Возвращает её текст.

    Нужна потому, что вход в hh начинается со страницы выбора роли: полей там
    нет, есть кнопка «Войти». Сценарий, умеющий только вводить текст, на ней
    останавливался и просил человека прислать то, чего на снимке нет.
    """
    return page.evaluate(
        """([advance, skip]) => {
            const ok = new RegExp(advance, 'i');
            const no = new RegExp(skip, 'i');
            const controls = [...document.querySelectorAll(
                'button, [role=button], input[type=submit], a')]
                .filter(el => el.offsetParent !== null && !el.disabled);
            const hit = controls.find(el => {
                const text = (el.innerText || el.value || '').trim();
                return text && ok.test(text) && !no.test(text);
            });
            if (!hit) return '';
            hit.click();
            return (hit.innerText || hit.value || '').trim().slice(0, 40);
        }""",
        [advance, skip],
    )


def login(send_photo, send_message, headless: bool = True) -> dict:
    """Открыть вход в hh и довести его с помощью человека.

    `send_photo(png, caption)` и `send_message(text)` приходят из Telegram-слоя,
    поэтому модуль ничего не знает о токенах и чатах.
    """
    from playwright.sync_api import sync_playwright
    from scout.collector import PROFILE

    steps: list[str] = []
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=headless,
            viewport={"width": 1380, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://hh.ru/account/login", wait_until="domcontentloaded",
                      timeout=45_000)
            page.wait_for_timeout(2500)

            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            if "выход" in body.lower() or "/applicant" in page.url:
                return {"ok": True, "steps": ["Сессия уже жива"], "note": "Вход не требуется"}

            advances = 0
            for stage in ("start", "captcha", "code"):
                # Шаги без полей сценарий проходит сам: выбор роли, кнопка
                # «Войти», промежуточные подтверждения. Человека зовём только
                # когда на странице действительно надо что-то ввести.
                while advances < MAX_ADVANCES and _first_input(page) is None:
                    pressed = _advance(page, ADVANCE, SKIP)
                    if not pressed:
                        break
                    advances += 1
                    steps.append(f"кнопка «{pressed}»")
                    page.wait_for_timeout(4000)
                    if "/applicant" in page.url or "/account/login" not in page.url:
                        send_message("Вход выполнен.")
                        return {"ok": True, "steps": steps}

                send_photo(page.screenshot(full_page=False),
                           "Вход в hh. Посмотри снимок и пришли то, что на нём просят: "
                           "телефон или почту, разгадку картинки или код подтверждения. "
                           "Ответ вводится в первое пустое поле страницы.")
                waiting = ask(stage)
                answer = waiting.wait()
                if answer is None:
                    send_message("Ответа не было пять минут, вход отменён.")
                    return {"ok": False, "steps": steps, "note": "истекло ожидание"}

                kind = classify(answer)
                steps.append(kind)
                # Поле выбирается по странице, а не по виду ответа. Прежняя
                # логика «цифры - значит код» ломалась на первом же шаге: hh
                # просит телефон, телефон состоит из цифр, и ответ уходил в
                # поле кода, которого на странице ещё нет.
                target = _first_input(page)
                if target is None:
                    send_message("На странице нет поля для ввода. Пришли, что видишь "
                                 "на снимке, или войди вручную.")
                    return {"ok": False, "steps": steps, "note": "поле не найдено"}
                # Телефон и код hh принимает только цифрами, остальное - как есть.
                value = digits(answer) if kind == "code" else answer.strip()
                target.fill(value)
                page.keyboard.press("Enter")
                page.wait_for_timeout(5000)
                # После ввода hh часто показывает ещё одну кнопку подтверждения.
                if _first_input(page) is None and advances < MAX_ADVANCES:
                    pressed = _advance(page, ADVANCE, SKIP)
                    if pressed:
                        advances += 1
                        steps.append(f"кнопка «{pressed}»")
                        page.wait_for_timeout(4000)

                if "/applicant" in page.url or "/account/login" not in page.url:
                    send_message("Вход выполнен.")
                    return {"ok": True, "steps": steps}

            send_message("Вход не завершился после трёх шагов. Нужен ручной вход.")
            return {"ok": False, "steps": steps, "note": "шаги кончились"}
        finally:
            clear()
            try:
                context.close()
            except Exception:
                pass
