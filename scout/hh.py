# -*- coding: utf-8 -*-
"""Клиент API hh.ru.

⚠️ Анонимный доступ hh закрыл - на запросы без токена приходит 403.
Токен приложения бесплатный: dev.hh.ru -> создать приложение -> взять
client credentials. Кладётся в переменную окружения HH_TOKEN.

Откликаться отсюда нельзя и не планируется: решение об отклике за человеком.
Инструмент только находит и оценивает.
"""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

API = "https://api.hh.ru"
UA = "vacancy-scout/1.0 (personal job search tool)"
MOSCOW = "1"


class _Strip(HTMLParser):
    """Описание вакансии приходит с html-разметкой, для анализа нужен текст."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("li", "p", "br"):
            self.parts.append("\n")


def strip_html(html: str) -> str:
    parser = _Strip()
    parser.feed(html or "")
    text = "".join(parser.parts)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


@dataclass
class Vacancy:
    id: str
    name: str
    employer: str
    url: str
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    schedule: str | None
    experience: str | None
    published: str
    description: str = ""
    key_skills: list[str] = field(default_factory=list)

    @property
    def salary_text(self) -> str:
        if not self.salary_from and not self.salary_to:
            return "не указана"
        thousand = lambda v: f"{v // 1000}к"
        if self.salary_from and self.salary_to:
            return f"{thousand(self.salary_from)}-{thousand(self.salary_to)}"
        if self.salary_from:
            return f"от {thousand(self.salary_from)}"
        return f"до {thousand(self.salary_to)}"

    @property
    def haystack(self) -> str:
        """Всё, по чему ищем совпадения."""
        return f"{self.name}\n{self.description}\n{' '.join(self.key_skills)}"


class NeedsToken(RuntimeError):
    """hh ответил 403: нужен токен приложения."""


def _get(path: str, params: dict) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    headers = {"User-Agent": UA}
    token = os.environ.get("HH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            import json
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise NeedsToken(
                "hh вернул 403. Нужен токен приложения: зарегистрировать на dev.hh.ru "
                "и положить в переменную окружения HH_TOKEN."
            ) from error
        raise


def search(text: str, area: str = MOSCOW, per_page: int = 20, pages: int = 1) -> list[Vacancy]:
    """Ищет вакансии по строке запроса. Описание подтягивается отдельно - см. enrich."""
    found: list[Vacancy] = []
    for page in range(pages):
        data = _get("/vacancies", {
            "text": text, "area": area, "per_page": per_page,
            "page": page, "order_by": "publication_time",
        })
        for item in data.get("items", []):
            salary = item.get("salary") or {}
            found.append(Vacancy(
                id=item["id"],
                name=item["name"],
                employer=(item.get("employer") or {}).get("name", "не указан"),
                url=item.get("alternate_url", ""),
                salary_from=salary.get("from"),
                salary_to=salary.get("to"),
                currency=salary.get("currency"),
                schedule=(item.get("schedule") or {}).get("name"),
                experience=(item.get("experience") or {}).get("name"),
                published=item.get("published_at", "")[:10],
            ))
        if page + 1 >= data.get("pages", 1):
            break
        time.sleep(0.25)   # вежливость к чужому API
    return found


def enrich(vacancy: Vacancy) -> Vacancy:
    """Догружает полное описание и ключевые навыки."""
    data = _get(f"/vacancies/{vacancy.id}", {})
    vacancy.description = strip_html(data.get("description", ""))
    vacancy.key_skills = [s["name"] for s in data.get("key_skills", [])]
    return vacancy
