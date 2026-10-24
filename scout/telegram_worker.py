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
COMMANDS = {"/start", "/status", "/preview", "/prepare", "/questions", "/login", "/apply"}

# Сколько секунд команда считается свежей. Окно защищает от повтора старой
# команды, когда Telegram отдаёт накопленную очередь. Прежние 120 секунд были
# слишком узкими: после перезапуска сервиса команда человека терялась молча,
# и 23.10 на этом потерялись /login и /status. Накопленная очередь теперь
# отбрасывается при старте опроса, поэтому окно можно держать шире.
FRESH_SECONDS = 600


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
            or len(text) > 64 or not 0 <= now.timestamp() - stamp <= FRESH_SECONDS):
        return None
    if text in COMMANDS:
        return text
    # Пока идёт вход в hh, короткий текст - это ответ человека: код из цифр
    # или разгадка картинки. Остальные сообщения по-прежнему игнорируются.
    from scout import hh_login
    if hh_login.awaiting() is not None and 1 <= len(text) <= 32:
        return "::answer::" + text
    return None


def refusal_reason(update: dict, allowed_chat_id: int,
                   now: datetime | None = None) -> str:
    """Какая именно проверка отвергла сообщение. Только для журнала.

    Общая строка «не команда, чужой чат или старше срока» покрывала семь
    разных условий, и разобрать по ней молчание бота было нельзя. Содержимое
    сообщения сюда не попадает: ответы на вход содержат коды подтверждения.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(update, dict):
        return "обновление не словарь"
    message = update.get("message")
    if not isinstance(message, dict):
        return "в обновлении нет message (правка, реакция или служебное)"
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = message.get("text")
    stamp = message.get("date")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return f"чат не личный: {chat.get('type')!r}"
    if chat.get("id") != allowed_chat_id:
        return f"чужой чат: {chat.get('id')!r} вместо {allowed_chat_id!r}"
    if not isinstance(sender, dict) or sender.get("id") != allowed_chat_id:
        return f"чужой отправитель: {sender.get('id')!r}"
    if sender.get("is_bot") is not False:
        return f"отправитель помечен ботом: {sender.get('is_bot')!r}"
    if not isinstance(text, str):
        return "сообщение без текста"
    if len(text) > 64:
        return f"текст длиной {len(text)} символов"
    if not isinstance(stamp, int):
        return "нет отметки времени"
    age = now.timestamp() - stamp
    if age < 0:
        return f"отметка времени в будущем на {abs(age):.0f} с"
    if age > FRESH_SECONDS:
        return f"старше окна: {age:.0f} с при пределе {FRESH_SECONDS}"
    from scout import hh_login
    if hh_login.awaiting() is None:
        return "текст не команда и вход сейчас не запрашивается"
    return "длина ответа вне 1-32 символов"


def handle_update(update: dict, allowed_chat_id: int, send,
                  status, preview, now: datetime | None = None,
                  prepare=None, questions=None, login=None, apply_run=None) -> bool:
    command = allowed_command(update, allowed_chat_id, now=now)
    if command is None:
        return False
    if command.startswith("::answer::"):
        from scout import hh_login
        outcome = hh_login.deliver(command[len("::answer::"):])
        send(allowed_chat_id, outcome["note"])
        return True
    if command == "/login":
        outcome = login() if login else {"ok": False, "note": "Вход не настроен"}
        send(allowed_chat_id, "Вход запущен, жди снимок экрана."
             if outcome.get("started") else str(outcome.get("note") or "Вход недоступен"))
        return True
    if command == "/apply":
        outcome = apply_run() if apply_run else {"mode": "dry-run", "ready": 0, "results": []}
        sent = sum(1 for item in outcome.get("results", []) if item.get("sent"))
        send(allowed_chat_id, f"Отклики, режим {outcome.get('mode')}: готовых "
             f"{outcome.get('ready', 0)}, отправлено {sent}.")
        return True
    if command == "/start":
        message = "Приватный бот разведчика подключён. Команды: /status, /preview, /prepare, /questions, /login, /apply."
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
    from scout import apply, response_form, runner, store
    from scout.application_plan import preview as application_preview
    from scout.question_plan import pending_questions

    offset: int | None = None
    misses = 0

    # Накопленная очередь отбрасывается до первой обработки: команды, посланные
    # пока сервис лежал, выполнять нельзя - человек уже не ждёт их результата.
    try:
        backlog = bot.get_updates(offset=None, timeout=0)
        for update in backlog:
            update_id = update.get("update_id") if isinstance(update, dict) else None
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
        if backlog:
            log.info("telegram: отброшено накопленных сообщений: %s", len(backlog))
    except TelegramError:
        pass                      # нет связи - начнём с нуля на первом круге

    while not _stop.is_set():
        try:
            updates = bot.get_updates(offset=offset, timeout=10)
            misses = 0
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)
                # Отклонённое сообщение исчезало без следа, и молчание бота
                # нельзя было отличить от мёртвого потока опроса. Текст в лог
                # не попадает: ответы на вход содержат коды подтверждения.
                taken = allowed_command(update, chat_id)
                if taken is None:
                    log.info("telegram: сообщение отклонено - %s",
                             refusal_reason(update, chat_id))
                elif taken.startswith("::answer::"):
                    log.info("telegram: принят ответ для входа")
                else:
                    log.info("telegram: принята команда %s", taken)
                handle_update(
                    update, chat_id, bot.send_message, runner.status,
                    lambda: application_preview(store.list_finds(), response_form.all_forms()),
                    prepare=runner.prepare_letters,
                    questions=lambda: pending_questions(store.list_finds(), response_form.all_forms()),
                    login=lambda: runner.start_login(
                        lambda png, caption: bot.send_photo(chat_id, png, caption),
                        lambda text: bot.send_message(chat_id, text)),
                    apply_run=lambda: apply.apply_batch(limit=5, dry_run=not apply.live_allowed()),
                )
        except TelegramError:
            # Без VPN Telegram недоступен, и опрос падает каждые 15 секунд -
            # лог забивался одинаковыми строками. Пауза растёт до пяти минут,
            # а в лог идёт только первая неудача и каждая десятая.
            misses += 1
            if misses == 1 or misses % 10 == 0:
                log.warning("Telegram API недоступен (подряд: %d)", misses)
            _stop.wait(min(15 * misses, 300))
            continue
        except Exception:
            log.exception("Ошибка обработчика Telegram без содержимого сообщения")
            _stop.wait(15)


def channel() -> tuple:
    """Пара функций отправки в личный чат, или (None, None) без настройки.

    Нужна фоновому кругу откликов: когда сессия hh слетает посреди пачки,
    человека надо позвать туда же, где он отвечает на вход. Токен и chat id
    остаются внутри модуля, наружу уходят только замыкания.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not raw_chat_id:
        return (None, None)
    try:
        chat_id = int(raw_chat_id)
        if chat_id <= 0:
            return (None, None)
        bot = TelegramClient(token)
    except ValueError:
        return (None, None)
    return (lambda png, caption="": bot.send_photo(chat_id, png, caption),
            lambda text: bot.send_message(chat_id, text))


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
