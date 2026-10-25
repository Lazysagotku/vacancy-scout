# -*- coding: utf-8 -*-
"""Минимальный клиент Telegram для приватной привязки бота.

Токен передаётся через окружение. Исключения намеренно не содержат URL:
у Telegram токен находится в пути Bot API.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from urllib import request


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, opener=None):
        if not token or any(character.isspace() for character in token):
            raise ValueError("Токен Telegram отсутствует или повреждён")
        self._token = token
        self._opener = opener or request.urlopen

    def _call(self, method: str, payload: dict | None = None) -> object:
        if method not in {"getMe", "getUpdates", "sendMessage"}:
            raise ValueError("Метод Telegram запрещён")
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=15) as response:
                data = json.load(response)
        except Exception as problem:
            # Причина нужна в тексте: «недоступен» одинаково означало и обрыв
            # сети, и отозванный токен, и запрет по стране. Из-за этого бот
            # молчал три недели, а лог показывал одну и ту же строку. Сам URL
            # в сообщение не попадает: в нём лежит токен.
            raise TelegramError(
                f"Telegram API не ответил на {method}: {type(problem).__name__}: "
                f"{str(problem)[:120]}") from None
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramError("Telegram API отклонил запрос")
        return data.get("result")

    def get_me(self) -> dict:
        result = self._call("getMe")
        if not isinstance(result, dict) or not result.get("id"):
            raise TelegramError("Telegram вернул неверный ответ getMe")
        return {"id": result["id"], "username": result.get("username") or ""}

    def get_updates(self, limit: int = 100, offset: int | None = None,
                    timeout: int = 0) -> list[dict]:
        payload: dict = {"limit": max(1, min(limit, 100)),
                         "timeout": max(0, min(timeout, 10)),
                         "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramError("Telegram вернул неверный список обновлений")
        return result

    def send_message(self, chat_id: int, message: str) -> None:
        if not isinstance(chat_id, int) or chat_id <= 0:
            raise ValueError("Ожидается личный chat id")
        if not 1 <= len(message) <= 4096:
            raise ValueError("Неверная длина сообщения")
        self._call("sendMessage", {"chat_id": chat_id, "text": message,
                                   "protect_content": True})

    def send_photo(self, chat_id: int, png: bytes, caption: str = "") -> None:
        """Передать PNG только в привязанный вызывающей стороной личный чат."""
        if not isinstance(chat_id, int) or chat_id <= 0:
            raise ValueError("Ожидается личный chat id")
        if (not isinstance(png, bytes) or not png.startswith(b"\x89PNG\r\n\x1a\n")
                or len(png) > 10_000_000):
            raise ValueError("Ожидается PNG не более 10 МБ")
        if len(caption) > 1024:
            raise ValueError("Слишком длинная подпись")
        boundary = "codex" + secrets.token_hex(12)
        parts = []
        for name, value in (("chat_id", str(chat_id)), ("protect_content", "true"),
                            ("caption", caption)):
            parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                          f"{value}\r\n").encode("utf-8"))
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                      "filename=\"hh-login.png\"\r\nContent-Type: image/png\r\n\r\n").encode("utf-8"))
        parts.extend((png, f"\r\n--{boundary}--\r\n".encode("ascii")))
        req = request.Request(
            f"https://api.telegram.org/bot{self._token}/sendPhoto",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=20) as response:
                data = json.load(response)
        except Exception as problem:
            raise TelegramError(
                f"Telegram API не принял снимок: {type(problem).__name__}: "
                f"{str(problem)[:120]}") from None
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramError("Telegram API отклонил изображение")


def recent_private_starts(updates: list[dict], now: datetime | None = None,
                          max_age_seconds: int = 600) -> list[int]:
    """Только недавние /start в личных чатах от людей, без содержимого сообщений."""
    now = now or datetime.now(timezone.utc)
    current = int(now.timestamp())
    result: list[int] = []
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict) or message.get("text") != "/start":
            continue
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        stamp = message.get("date")
        if (chat.get("type") != "private" or not isinstance(chat_id, int)
                or chat_id <= 0 or sender.get("id") != chat_id
                or sender.get("is_bot") is not False or not isinstance(stamp, int)
                or not 0 <= current - stamp <= max_age_seconds):
            continue
        if chat_id not in result:
            result.append(chat_id)
    return result
