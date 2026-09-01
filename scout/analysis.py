# -*- coding: utf-8 -*-
"""Разбор находок: описание, оценка, черновик письма.

Вынесен из веб-слоя, потому что разбором занимается и фоновый скаут,
и человек по кнопке - логика должна быть одна.
"""

from __future__ import annotations

from datetime import datetime

from scout import collector, store
from scout.hh import Vacancy
from scout.letter import draft
from scout.scoring import score


def _save(find: dict, description: str) -> dict:
    vacancy = Vacancy(
        id=find["id"], name=find["name"], employer=find.get("employer") or "",
        url=find.get("url") or "", salary_from=find.get("salary_from"),
        salary_to=find.get("salary_to"), currency="RUR", schedule=None,
        experience=find.get("experience"), published=find.get("found_at", "")[:10],
        description=description,
    )
    verdict = score(vacancy)
    store.update_find(
        find["id"],
        status="analyzed",
        score=verdict.score,
        verdict=verdict.verdict,
        track=verdict.track,
        matched=", ".join(name for name, _ in verdict.matched),
        gaps="; ".join(verdict.gaps),
        blockers="; ".join(verdict.blockers),
        notes="; ".join(verdict.notes),
        letter=draft(vacancy, verdict),
        description=description,
        analyzed_at=datetime.now().isoformat(timespec="seconds"),
    )
    return [f for f in store.list_finds() if f["id"] == find["id"]][0]


def analyze_find(vacancy_id: str, deep: bool = True) -> dict:
    """Разбирает одну находку. Используется кнопкой в интерфейсе."""
    items = [f for f in store.list_finds() if f["id"] == vacancy_id]
    if not items:
        raise LookupError(f"Находка {vacancy_id} не найдена")
    find = items[0]

    description = find.get("description") or ""
    if deep and not description:
        description = collector.fetch_descriptions([vacancy_id]).get(vacancy_id, "")
    return _save(find, description)


def analyze_many(finds: list[dict]) -> int:
    """Разбирает пачку в одном браузере. Возвращает число разобранных."""
    todo = [f for f in finds if not (f.get("description") or "")]
    descriptions = collector.fetch_descriptions([f["id"] for f in todo]) if todo else {}

    done = 0
    for find in finds:
        description = find.get("description") or descriptions.get(find["id"], "")
        _save(find, description)
        if description:
            done += 1
    return done
