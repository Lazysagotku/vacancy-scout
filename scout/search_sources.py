# -*- coding: utf-8 -*-
"""Устойчивые поисковые URL для трёх целевых направлений.

Подборка по резюме может истечь или вернуть 404. Эти запросы дают базовую
выдачу и работают независимо от сохранённой ссылки в настройках.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class SearchSource:
    track: str
    title: str
    url: str


_QUERIES = (
    ("python", "Python-разработчик", "Python разработчик"),
    ("python", "FastAPI backend", "FastAPI backend"),
    ("python", "Python автоматизация", "Python автоматизация"),
    ("support", "Инженер сопровождения", "инженер сопровождения"),
    ("support", "Техническая поддержка L2/L3", "техническая поддержка L2 L3"),
    ("csharp", "C#/.NET разработчик", "C# .NET разработчик"),
    # Решение Ивана 24.10: C#-резюме переделано в ИИ, поиск идёт за ним.
    # Формулировки взяты по частоте в 594 собранных ИИ-вакансиях: «llm» 13%,
    # «ai engineer» 5,9%, «ai-инженер» 4,2%, «ai-агент» 2,5%.
    ("ai", "AI-инженер", "AI инженер"),
    ("ai", "LLM-разработчик", "LLM разработчик"),
    ("ai", "AI-агенты и RAG", "AI агент RAG"),
)


def built_in_sources() -> list[SearchSource]:
    sources = []
    for track, title, text in _QUERIES:
        params = {"text": text, "area": "1", "order_by": "publication_time"}
        if track == "csharp":
            params["experience"] = "between1And3"
        sources.append(SearchSource(track, title,
                                    "https://hh.ru/search/vacancy?" + urlencode(params)))
    return sources
