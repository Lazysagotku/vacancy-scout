# -*- coding: utf-8 -*-
"""Перепроверка разобранных вакансий: что ушло в архив и что не открывается.

Находка живёт в базе месяцами, а вакансия на hh закрывается за неделю. Из-за
этого короткий список наполняется мёртвыми карточками: часть в архиве, часть
отдаёт 404, часть просто не открывается. Этот модуль проходит по разобранным
находкам и приводит их статус в соответствие с тем, что сейчас на hh.

Сетевой работы здесь нет: страницы читает браузерный сборщик, этот модуль
только решает, кого проверять и что записать по ответу.

Отдельно живёт `refresh_resumes`: пересчёт приложенного резюме по текущим
правилам. Нужен после правок `profile.resume_for` - старые карточки иначе
остаются с резюме, выбранным по прежней логике.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from scout import profile, store
from scout.policy import MAX_AGE_DAYS, MIN_SCORE

log = logging.getLogger(__name__)

# Сколько карточек проверяем за один заход. Больше - дольше держим браузер и
# выше шанс поймать проверку hh на автоматический обход.
BATCH = 40

# Карточку младше этого срока перепроверять незачем: она только что собрана.
MIN_AGE = timedelta(hours=24)


def _parsed(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None


def on_short_list(find: dict, now: datetime) -> bool:
    """Попадает ли карточка в короткую выдачу, которую человек видит.

    Те же два условия, по которым её отбирает `policy.select_mix`: достаточный
    балл и возраст в пределах окна выдачи. Проверять в первую очередь надо
    именно их - мёртвая карточка в хвосте базы никому не показывается, а
    мёртвая в рекомендациях стоит человеку впустую открытой вкладки.
    """
    if (find.get("score") or 0) < MIN_SCORE:
        return False
    found = _parsed(find.get("found_at"))
    return found is not None and now - found <= timedelta(days=MAX_AGE_DAYS)


def stale_first(finds: list[dict], now: datetime | None = None,
                limit: int = BATCH) -> list[dict]:
    """Кого перепроверять: разобранные, живые, давно не смотренные.

    Сначала идут карточки из короткой выдачи - их человек открывает. Внутри
    каждой группы порядок от самых старых проверок к свежим, чтобы заход
    закрывал самый протухший край.

    Так видимые 280 карточек проходят полный круг меньше чем за сутки, а
    остальные пять тысяч доедаются остатком пачки.
    """
    now = now or datetime.now()
    todo = []
    for find in finds:
        if find.get("status") != "analyzed" or find.get("archived_at"):
            continue
        seen = _parsed(find.get("checked_at")) or _parsed(find.get("analyzed_at"))
        if seen is None or now - seen < MIN_AGE:
            continue
        todo.append((0 if on_short_list(find, now) else 1, seen, find))
    todo.sort(key=lambda row: (row[0], row[1]))
    return [find for _, _, find in todo[:max(0, limit)]]


def verdict_for(find: dict, details: dict | None) -> dict:
    """Что записать в базу по ответу страницы.

    Три исхода: вакансия в архиве, страница не открылась, вакансия жива.
    Пустой словарь означает «ничего не менять».
    """
    now = datetime.now().isoformat(timespec="seconds")
    if details is None:
        # Страница не отдалась. Один раз это сеть, три раза подряд - вакансии
        # уже нет. Снимаем с карточки статус разобранной только на третий раз,
        # иначе разведчик будет хоронить живые вакансии при обрыве связи.
        misses = int(find.get("check_misses") or 0) + 1
        if misses >= 3:
            return {"status": "dropped", "drop_reason": "страница не открывается",
                    "checked_at": now, "check_misses": misses}
        return {"checked_at": now, "check_misses": misses}

    if details.get("archived_at"):
        return {"status": "dropped", "archived_at": details["archived_at"],
                "drop_reason": f"в архиве с {details['archived_at']}",
                "checked_at": now, "check_misses": 0}

    return {"checked_at": now, "check_misses": 0}


def recheck(limit: int = BATCH, on_progress=None) -> dict:
    """Пройтись по протухшим карточкам и обновить их состояние."""
    from scout import collector

    todo = stale_first(store.list_finds("analyzed"), limit=limit)
    if not todo:
        return {"checked": 0, "archived": 0, "gone": 0, "alive": 0}

    details = collector.fetch_details([find["id"] for find in todo],
                                      on_progress=on_progress)
    counts = {"checked": 0, "archived": 0, "gone": 0, "alive": 0}
    for find in todo:
        fields = verdict_for(find, details.get(find["id"]))
        if not fields:
            continue
        store.update_find(find["id"], **fields)
        counts["checked"] += 1
        if fields.get("archived_at"):
            counts["archived"] += 1
        elif fields.get("status") == "dropped":
            counts["gone"] += 1
        else:
            counts["alive"] += 1
    log.info("перепроверка: %s", counts)
    return counts


def refresh_resumes(dry_run: bool = True) -> dict:
    """Пересчитать приложенное резюме по текущим правилам.

    Нужен после правок подбора резюме. По умолчанию ничего не пишет: сначала
    показывает, сколько карточек поменяется.
    """
    changes: list[tuple[str, str, str]] = []
    for find in store.list_finds("analyzed"):
        current = find.get("resume") or ""
        if not current:
            continue
        fresh = profile.resume_for(find.get("track"), find.get("name") or "",
                                   find.get("description") or "")
        if fresh != current:
            changes.append((find["id"], current, fresh))
            if not dry_run:
                # Письмо писалось под прежнее резюме, поэтому помечаем его на
                # переделку: зарплатная строка в нём теперь от другого трека.
                store.update_find(find["id"], resume=fresh, letter_kind="template")
    return {"dry_run": dry_run, "changed": len(changes),
            "examples": [{"id": i, "was": w, "now": n} for i, w, n in changes[:10]]}
