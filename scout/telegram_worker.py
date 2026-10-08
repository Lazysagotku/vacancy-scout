# -*- coding: utf-8 -*-
"""Приватные команды разведчика. Код входа hh пока не принимается."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from scout.telegram_bot import TelegramClient, TelegramError

log = logging.getLogger(__name__)
_worker_thread: threading.Thread | None = None
_stop = threading.Event()
_start_lock = threading.Lock()
COMMANDS = {"/start", "/status", "/preview", "/prepare", "/questions"}


def allowed_command(update: dict, allowed_chat_id: int,
                    now: datetime | None = None) -> str | None:
    """Проверить личный чат, отправителя, свежесть и точную команду."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = message.get("text")
    stamp = message.get("date")
    if (not isinstance(chat, dict) or not isinstance(sender, dict)
            or chat.get("type") != "private" or chat.get("id") != allowed_chat_id
            or sender.get("id") != allowed_chat_id or sender.get("is_bot") is not False
            or not isinstance(stamp, int) or not isinstance(text, str)
            or len(text) > 64 or not 0 <= now.timestamp() - stamp <= 120):
        return None
    return text if text in COMMANDS else None


def handle_update(update: dict, allowed_chat_id: int, send,
                  status, preview, now: datetime | None = None,
                  prepare=None, questions=None) -> bool:
    command = allowed_command(update, allowed_chat_id, now=now)
    if command is None:
        return False
    if command == "/start":
        message = "Приватный бот разведчика подключён. Команды: /status, /preview, /prepare, /questions."
    elif command == "/status":
        state = status()
        mode = "работает" if state.get("running") else "остановлен"
        phase = " Подготовка писем идёт." if state.get("preparing") else ""
        prepared = state.get("last_prepared") or 0
        message = (f"Разведчик: {mode}.{phase} Последняя подготовка: {prepared} писем. "
                   "Живая отправка откликов выключена.")
        if state.get("last_prepare_error"):
            message += f" Сбой подготовки: {state['last_prepare_error']}."
    elif command == "/preview":
        items = preview()
        ready = sum(bool(item.get("eligible")) for item in items)
        message = (f"Dry-run откликов: проверено {len(items)}, готово {ready}. "
                   "Отправка выключена.")
    elif command == "/prepare":
        outcome = prepare() if prepare else {"started": False, "reason": "Подготовка не настроена"}
        message = ("Подготовка писем запущена. Проверяй /status."
                   if outcome.get("started") else str(outcome.get("reason") or "Подготовка недоступна"))
    else:
        pending = questions() if questions else []
        if not pending:
            message = ("Свежих сохранённых анкет с вопросами нет. Автоматическое открытие форм "
                       "выключено; неизвестные вопросы проходи на hh вручную.")
        else:
            lines = ["Анкеты с вопросами - черновики ответов, отправка выключена:"]
            for item in pending[:5]:
                lines.append(f"{item['id']} - {item['name']}")
                for answer in item.get("answers", [])[:4]:
                    lines.append(f"{answer['number']}. {answer['question'][:220]}")
                    if answer.get("suggestion"):
                        lines.append(f"Черновик: {answer['suggestion'][:260]}")
                    else:
                        lines.append("Нужен ответ Ивана в форме hh.")
            message = "\n".join(lines)[:3500]
    send(allowed_chat_id, message)
    return True


def _poll(bot: TelegramClient, chat_id: int) -> None:
    from scout import response_form, runner, store
    from scout.application_plan import preview as application_preview
    from scout.question_plan import pending_questions

    offset: int | None = None
    while not _stop.is_set():
        try:
            updates = bot.get_updates(offset=offset, timeout=10)
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)
                handle_update(
                    update, chat_id, bot.send_message, runner.status,
                    lambda: application_preview(store.list_finds(), response_form.all_forms()),
                    prepare=runner.prepare_letters,
                    questions=lambda: pending_questions(store.list_finds(), response_form.all_forms()),
                )
        except TelegramError:
            log.warning("Telegram API недоступен")
            _stop.wait(15)
        except Exception:
            log.exception("Ошибка обработчика Telegram без содержимого сообщения")
            _stop.wait(15)


def start_if_configured() -> bool:
    """Запустить polling только после явной привязки chat id в .env."""
    global _worker_thread
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not raw_chat_id:
        return False
    try:
        chat_id = int(raw_chat_id)
        if chat_id <= 0:
            return False
        bot = TelegramClient(token)
    except ValueError:
        return False
    with _start_lock:
        if _worker_thread and _worker_thread.is_alive():
            return True
        _stop.clear()
        _worker_thread = threading.Thread(target=_poll, args=(bot, chat_id),
                                          daemon=True, name="scout-telegram")
        _worker_thread.start()
    return True


def stop() -> None:
    _stop.set()
