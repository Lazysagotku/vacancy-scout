# -*- coding: utf-8 -*-
"""Разбор находок: описание, оценка, черновик письма.

Вынесен из веб-слоя, потому что разбором занимается и фоновый скаут,
и человек по кнопке - логика должна быть одна.
"""

from __future__ import annotations

from datetime import datetime

from scout import collector, profile, store
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
    # Если форму отклика уже разведывали, письмо собирается с оглядкой
    # на её вопросы: то, что спросят отдельным полем, в письме не нужно.
    from scout import response_form
    form = response_form.known(find["id"])
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
        letter=draft(vacancy, verdict, form),
        resume=profile.resume_for(verdict.track, find.get("name", "") + " " + description),
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


def peek_forms(finds: list[dict], limit: int = 8) -> int:
    """Снимает вопросы формы отклика у самых подходящих находок.

    Открывать форму у каждой вакансии дорого: это отдельная загрузка
    страницы плюс клик. Поэтому берём только тех, к кому реально пойдёт
    отклик - у остальных вопросы всё равно не понадобятся.
    """
    from scout import response_form

    worth = [f for f in finds
             if (f.get("score") or 0) >= 60 and not response_form.known(f["id"])]
    done = 0
    for find in worth[:limit]:
        try:
            response_form.peek(find["id"])
            done += 1
        except Exception:
            break            # сессия отвалилась - дальше смысла нет
    return done


def analyze_many(finds: list[dict], on_progress=None) -> int:
    """Разбирает пачку в одном браузере. Возвращает число разобранных.

    Пачка на две сотни вакансий идёт минутами, поэтому ход разбора
    сообщается наверх: человек должен видеть, что работа идёт, а не
    гадать, завис скаут или нет.
    """
    todo = [f for f in finds if not (f.get("description") or "")]
    descriptions = (
        collector.fetch_descriptions([f["id"] for f in todo], on_progress=on_progress)
        if todo else {}
    )

    done = 0
    for find in finds:
        description = find.get("description") or descriptions.get(find["id"], "")
        _save(find, description)
        if description:
            done += 1
    return done
