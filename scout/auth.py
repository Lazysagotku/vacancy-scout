# -*- coding: utf-8 -*-
"""Защита сервиса паролем.

Нужна с того момента, как сервис вышел в интернет через туннель. Ссылки
лежат в портфолио - пусть и в скрытом разделе, но исходник страницы
видит любой. Внутри трекера вилки, заметки о компаниях и фидбек
с собеседований, внутри разведчика - черновики писем.

Пароль лежит в переменной окружения. Пока её нет, защита выключена:
на localhost она мешала бы, а туннель без пароля запускать не надо.
"""

from __future__ import annotations

import hmac
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

ENV_USER = "APP_USER"
ENV_PASS = "APP_PASSWORD"

# Локальные адреса пускаем без пароля: с этой же машины сервисом
# пользуются постоянно, и вводить пароль каждый раз незачем.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _expected() -> tuple[str, str] | None:
    password = os.getenv(ENV_PASS, "").strip()
    if not password:
        return None
    return os.getenv(ENV_USER, "ivan").strip() or "ivan", password


class BasicAuth(BaseHTTPMiddleware):
    """Спрашивает логин и пароль у всех, кто пришёл не с этой машины."""

    async def dispatch(self, request, call_next):
        creds = _expected()
        if creds is None:
            return await call_next(request)

        client = request.client.host if request.client else ""
        if client in LOCAL_HOSTS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            import base64
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
                user, _, password = raw.partition(":")
            except Exception:
                user = password = ""
            want_user, want_pass = creds
            # compare_digest, чтобы по времени ответа нельзя было подобрать
            if (hmac.compare_digest(user, want_user)
                    and hmac.compare_digest(password, want_pass)):
                return await call_next(request)

        # realm уходит в HTTP-заголовок, а там разрешён только latin-1:
        # кириллица роняет ответ на кодировании.
        return Response(
            "Нужен пароль", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Private"'},
        )


def suggest_password() -> str:
    """Предлагает пароль для первой настройки."""
    return secrets.token_urlsafe(12)
