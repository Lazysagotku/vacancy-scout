# -*- coding: utf-8 -*-
"""Хранилище находок.

Скаут копит вакансии сам, между запусками. Дальше находка проходит путь:
new -> analyzed -> sent -> tracked. Ссылка на исходную выдачу нужна только
до разбора: после него она уже не даёт ничего, что не лежало бы в карточке.

SQLite намеренно: у скаута одна пишущая сторона и никакой конкуренции,
тащить сюда PostgreSQL было бы усложнением без выгоды.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "scout.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS finds (
    id           TEXT PRIMARY KEY,          -- id вакансии на hh
    name         TEXT NOT NULL,
    employer     TEXT,
    url          TEXT,
    salary_from  INTEGER,
    salary_to    INTEGER,
    experience   TEXT,
    remote       INTEGER DEFAULT 0,
    found_at     TEXT NOT NULL,
    query        TEXT,                      -- по какому запросу нашлась

    status       TEXT NOT NULL DEFAULT 'new',   -- new | analyzed | sent | tracked | dropped
    drop_reason  TEXT,                          -- почему отсеяли: важно, чтобы не пересматривать
    score        INTEGER,
    verdict      TEXT,
    track        TEXT,
    matched      TEXT,
    gaps         TEXT,
    blockers     TEXT,
    notes        TEXT,
    letter       TEXT,
    description  TEXT,                      -- полное описание, если догружали
    analyzed_at  TEXT,
    sent_at      TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started   TEXT NOT NULL,
    finished  TEXT,
    found     INTEGER DEFAULT 0,
    fresh     INTEGER DEFAULT 0,
    error     TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULTS = {
    "search_url": "",          # ссылки на выдачу hh, по одной на строку
    "interval_minutes": "180",
    "enabled": "0",
    # Сколько вакансий открывать за один прогон. Открыть - секунды браузера
    # и ноль токенов, поэтому много. Потолок нужен из-за hh: сотни страниц
    # подряд с залогиненного аккаунта - это капча на аккаунте Ивана.
    "analyze_limit": "120",
    # Сколько писем через Claude за прогон, лучшие по оценке первыми,
    # остальным - кнопка в карточке. Иван 12.09: «брать количеством» -
    # 30 на прогон при восьми прогонах в сутки, потолок 240 в день.
    "letter_limit": "30",
}


@contextmanager
def connect():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# Колонки, появившиеся после первой схемы. База у Ивана одна и живая,
# пересоздавать её нельзя - поэтому добавляем по одной, если ещё нет.
LATER_COLUMNS = {
    "review":           "TEXT",              # мнение: рекомендую или нет и почему
    "form_answers":     "TEXT",              # ответы на вопросы формы отклика
    "resume":           "TEXT",              # какое из трёх резюме прикладывать
    "letter_kind":      "TEXT DEFAULT ''",   # template - абзацы из letter.py, claude - под вакансию
    # Шапка вакансии со страницы hh (12.09): оформление, график, формат -
    # то, что Иван смотрит первым делом, и чего в карточке выдачи нет
    "employment":       "TEXT",              # полная занятость, частичная
    "hiring":           "TEXT",              # оформление: ТД, ГПХ, самозанятый
    "schedule":         "TEXT",              # график: 5/2, 2/2, сменный
    "hours":            "TEXT",              # рабочие часы
    "work_format":      "TEXT",              # удалённо, гибрид, на месте
    "employer_rating":  "REAL",              # рейтинг работодателя на hh
    "employer_reviews": "INTEGER",           # число отзывов
    "key_skills":       "TEXT",              # «Ключевые навыки» через запятую
    "archived_at":      "TEXT",              # вакансия в архиве: с какого числа
    # 14.09: закреплённые Иваном сверху и мнение отдельным полем для группировки
    "pinned_at":        "TEXT",              # нажал «переписать под вакансию» - карточка держится вверху
    "opinion":          "TEXT",              # Рекомендую | Под вопросом + | Под вопросом | Под вопросом - | Не рекомендую
}


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {row[1] for row in con.execute("PRAGMA table_info(finds)")}
        for name, kind in LATER_COLUMNS.items():
            if name not in cols:
                con.execute(f"ALTER TABLE finds ADD COLUMN {name} {kind}")
        for key, value in DEFAULTS.items():
            con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_find(vacancy_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM finds WHERE id = ?", (vacancy_id,)).fetchone()
    return dict(row) if row else None


def get_setting(key: str, default: str = "") -> str:
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def save_finds(items: list[dict], query: str = "") -> int:
    """Кладёт находки с предварительной оценкой по названию.

    Полный разбор требует захода в каждую вакансию, а это секунды на каждую.
    Поэтому при сборе считаем грубую оценку по заголовку и условиям: этого
    хватает, чтобы мусор ушёл вниз, а разбирать вручную только верх списка.
    Уже известные находки не трогаем - иначе затрём результат разбора.
    """
    from scout.prescore import prescore          # импорт здесь: избегаем цикла

    fresh = 0
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        for item in items:
            exists = con.execute("SELECT 1 FROM finds WHERE id = ?", (item["id"],)).fetchone()
            if exists:
                continue
            guess = prescore(item)
            con.execute(
                "INSERT INTO finds (id, name, employer, url, salary_from, salary_to, "
                "experience, remote, found_at, query, score, verdict, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], item["name"], item.get("employer"), item.get("url"),
                 item.get("salary_from"), item.get("salary_to"), item.get("experience"),
                 int(bool(item.get("remote"))), now, query,
                 guess["score"], guess["verdict"], guess["notes"]),
            )
            fresh += 1
    return fresh


def list_finds(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM finds"
    args: tuple = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    # Закреплённые (Иван нажал «переписать») - первыми, свежие закрепления выше;
    # дальше по оценке. Группировка по мнению делается в интерфейсе.
    sql += (" ORDER BY CASE WHEN pinned_at IS NULL THEN 1 ELSE 0 END, pinned_at DESC,"
            " CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, found_at DESC")
    with connect() as con:
        return [dict(row) for row in con.execute(sql, args)]


def update_find(vacancy_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{key} = ?" for key in fields)
    with connect() as con:
        con.execute(f"UPDATE finds SET {sets} WHERE id = ?", (*fields.values(), vacancy_id))


def drop(vacancy_id: str, reason: str = "") -> None:
    """Отсеивает находку. Она остаётся в базе - иначе следующий сбор принесёт её снова."""
    update_find(vacancy_id, status="dropped", drop_reason=reason or "не заинтересовала")


def known_ids() -> set[str]:
    """Все известные id, включая отсеянные: по ним отсекаются повторы при сборе."""
    with connect() as con:
        return {row["id"] for row in con.execute("SELECT id FROM finds")}


def counts() -> dict[str, int]:
    with connect() as con:
        rows = con.execute("SELECT status, COUNT(*) AS n FROM finds GROUP BY status")
        result = {row["status"]: row["n"] for row in rows}
        result["total"] = sum(result.values())
    return result


def start_run() -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO runs (started) VALUES (?)",
                          (datetime.now().isoformat(timespec="seconds"),))
        return cur.lastrowid


def finish_run(run_id: int, found: int, fresh: int, error: str = "") -> None:
    with connect() as con:
        con.execute(
            "UPDATE runs SET finished = ?, found = ?, fresh = ?, error = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), found, fresh, error or None, run_id),
        )


def last_runs(limit: int = 5) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]
