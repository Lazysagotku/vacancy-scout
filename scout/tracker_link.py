# -*- coding: utf-8 -*-
"""Перенос разобранной вакансии в трекер откликов.

Скаут отвечает за поиск, трекер - за ведение отклика. Как только отклик
отправлен, вакансия перестаёт быть находкой и становится процессом,
поэтому переезжает целиком и больше в скауте не редактируется.

Подключение берётся из .env трекера - отдельной копии пароля не заводим.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

TRACKER = Path(r"c:\projects\Новая папка (3)\vacancy-tracker")


class TrackerUnavailable(RuntimeError):
    """Трекер не найден или недоступен."""


def _prepare() -> None:
    if not TRACKER.exists():
        raise TrackerUnavailable(f"Не найден трекер: {TRACKER}")
    for path in (str(TRACKER), str(TRACKER / ".venv" / "Lib" / "site-packages")):
        if path not in sys.path:
            sys.path.insert(0, path)
    env = TRACKER / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def push(find: dict) -> int:
    """Заводит вакансию в трекере со статусом «отклик отправлен». Возвращает id."""
    _prepare()
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.database import engine
        from app.models import Company, Event, EventType, Status, Track, Vacancy, WorkFormat
    except Exception as error:
        raise TrackerUnavailable(f"Не удалось подключиться к трекеру: {error}") from error

    name = (find.get("employer") or "не указана").strip() or "не указана"
    # Из карточки hh к названию иногда прилипает рейтинг вида «4.6»
    name = re.sub(r"\s*\d\.\d\s*$", "", name).strip()

    track_map = {"dev": Track.dev, "support": Track.support, "ai": Track.ai}
    fmt = WorkFormat.remote if find.get("remote") else WorkFormat.unknown

    with Session(engine) as session:
        company = session.scalar(select(Company).where(Company.name == name))
        if company is None:
            company = Company(name=name)
            session.add(company)
            session.flush()

        existing = session.scalar(
            select(Vacancy).where(Vacancy.company_id == company.id,
                                  Vacancy.title == find["name"])
        )
        if existing:
            return existing.id

        note_parts = [f"[Найдено Скаутом, оценка {find.get('score')}]"]
        for key, label in (("matched", "совпало"), ("gaps", "пробелы"),
                           ("blockers", "блокеры"), ("notes", "внимание")):
            if find.get(key):
                note_parts.append(f"{label}: {find[key]}")

        vacancy = Vacancy(
            company=company,
            title=find["name"],
            status=Status.applied,
            priority=2 if (find.get("score") or 0) >= 70 else 3,
            track=track_map.get(find.get("track") or ""),
            work_format=fmt,
            salary_min=find.get("salary_from"),
            salary_max=find.get("salary_to"),
            source_url=find.get("url"),
            applied_at=date.today(),
            match_note="\n".join(note_parts),
            next_action="Ждать ответа",
        )
        session.add(vacancy)
        session.flush()
        session.add(Event(vacancy_id=vacancy.id, type=EventType.applied,
                          title="Отклик отправлен (перенос из Скаута)"))
        session.commit()
        return vacancy.id
