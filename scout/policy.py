# -*- coding: utf-8 -*-
"""Проверяемая политика короткого списка вакансий.

Скоринг оценивает совпадение навыков. Здесь определяется состав выдачи:
Python 60%, сопровождение 30%, C# 10%, с отдельным ограничением стажа C#.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta


MIX = {"python": 0.6, "support": 0.3, "csharp": 0.1}
MIX_ORDER = ("python", "support", "python", "csharp", "python",
             "support", "python", "python", "support", "python")
MIN_SCORE = 45
MAX_AGE_DAYS = 14
SALARY_MIN_NET = {"python": 170_000, "csharp": 150_000, "support": 200_000}
SALARY_COMFORT_NET = 200_000

SUPPORT_WORDS = (
    "поддержк", "сопровожден", "эксплуатац", "системный администратор",
    "инфраструктур", "devops", "sre", "support", "site reliability",
    # Роли эксплуатации, которые скоринг считал сильными, а политика теряла:
    # на базе 7315 находок 146 из 493 сильных получали пустой трек и не
    # попадали в короткую выдачу вообще - «Администратор баз данных»,
    # «Инженер linux», «Инженер мониторинга», «DBA». Общее слово «инженер»
    # сюда не добавляем: оно затянет QA и дата-инженеров в квоту поддержки.
    "администратор", "dba", "linux", "мониторинг",
)
PYTHON_WORDS = ("python", "питон", "django", "fastapi", "flask")
CSHARP_WORDS = ("c#", ".net", "dotnet", "asp.net", "си шарп")
DEVELOPER_WORDS = ("разработчик", "программист", "developer", "backend", "бэкенд")


def salary_line(track: str) -> str:
    """Согласованные ожидания по доходу без gross/net двусмысленности."""
    if track not in SALARY_MIN_NET:
        raise ValueError(f"Неизвестный зарплатный трек: {track}")
    low = SALARY_MIN_NET[track]
    if low == SALARY_COMFORT_NET:
        amount = "200 000"
    else:
        amount = f"{low // 1000} 000-200 000"
    return f"Ожидания по доходу - {amount} рублей на руки."


def salary_track_for_resume(resume: str) -> str:
    name = resume.casefold()
    if "python" in name:
        return "python"
    if "c#" in name or ".net" in name:
        return "csharp"
    return "support"


def salary_floor_for_find(find: dict, fallback: int = 160_000) -> int:
    """Ориентир для грубой оценки опубликованной суммы по целевому треку."""
    return SALARY_MIN_NET.get(classify_find(find), fallback)


def classify_find(find: dict) -> str | None:
    """Определить целевой трек по роли, затем по основному стеку."""
    title = (find.get("name") or "").casefold()
    body = (find.get("description") or "").casefold()
    if any(word in title for word in SUPPORT_WORDS):
        return "support"
    if any(word in title for word in PYTHON_WORDS):
        return "python"
    if any(word in title for word in CSHARP_WORDS):
        return "csharp"
    if any(word in title for word in DEVELOPER_WORDS):
        python_hits = sum(body.count(word) for word in PYTHON_WORDS)
        csharp_hits = sum(body.count(word) for word in CSHARP_WORDS)
        if python_hits > csharp_hits:
            return "python"
        if csharp_hits > python_hits:
            return "csharp"
    return None


def csharp_experience_ok(experience: str | None) -> bool:
    """В выдачу C# попадают вакансии с требованием 1-3 года."""
    value = (experience or "").casefold().strip()
    if value == "between1and3":
        return True
    normalized = value.replace("–", "-").replace("—", "-")
    return bool(re.search(r"(?:от\s*)?1\s*(?:года?\s*)?(?:до|-|\s+)\s*3(?!\d)\s*(?:лет|года?)?", normalized))


def _quotas(limit: int) -> dict[str, int]:
    whole = {track: int(limit * weight) for track, weight in MIX.items()}
    left = limit - sum(whole.values())
    by_remainder = sorted(MIX, key=lambda key: (-(limit * MIX[key] - whole[key]), list(MIX).index(key)))
    for track in by_remainder[:left]:
        whole[track] += 1
    return whole


def select_mix(finds: list[dict], limit: int = 30, now: datetime | None = None,
               max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    """Короткий список с жёсткими долями и без добивки шумом из других треков."""
    if limit <= 0:
        return []
    now = now or datetime.now()
    cutoff = now - timedelta(days=max_age_days)
    buckets: dict[str, list[dict]] = {track: [] for track in MIX}
    seen: set[str] = set()
    for find in finds:
        vacancy_id = str(find.get("id") or "")
        if not vacancy_id or vacancy_id in seen:
            continue
        seen.add(vacancy_id)
        if find.get("status") not in {"new", "analyzed"}:
            continue
        if find.get("archived_at") or (find.get("score") or 0) < MIN_SCORE:
            continue
        try:
            found_at = datetime.fromisoformat(str(find.get("found_at") or ""))
        except ValueError:
            continue
        if found_at < cutoff:
            continue
        track = classify_find(find)
        if track == "csharp" and not csharp_experience_ok(find.get("experience")):
            continue
        if track in buckets:
            buckets[track].append(find)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: ((item.get("score") or 0), item.get("found_at") or ""), reverse=True)
    quotas = _quotas(limit)
    selected: list[dict] = []
    used = {track: 0 for track in MIX}
    while any(used[track] < min(quotas[track], len(buckets[track])) for track in MIX):
        for track in MIX_ORDER:
            if used[track] < min(quotas[track], len(buckets[track])):
                selected.append(buckets[track][used[track]])
                used[track] += 1
    return selected
