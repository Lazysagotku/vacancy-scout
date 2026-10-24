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

# Разобранная карточка старше окна выдачи мертва примерно в двух случаях из
# трёх: 14.10 проверка 120 самых давно смотренных дала 82 архива. Тратить на
# такой хвост браузер дороже, чем закрыть его по возрасту - карточка остаётся
# в базе и возвращается кнопкой «Вернуть в список».
STALE_AFTER = timedelta(days=MAX_AGE_DAYS)

# Карточку, которую недавно видели живой, возраст не закрывает: она старая по
# дате находки, но проверена своими глазами.
ALIVE_TRUST = timedelta(days=7)


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


def page_missed(details: dict | None) -> bool:
    """Страница не отдалась.

    Сборщик на недоступной вакансии возвращает не None, а пустой разбор, и
    проверка `details is None` его не ловила: такая карточка записывалась живой
    с обнулённым счётчиком промахов. За два прогона, 150 проверок, «не
    открывается» не выпало ни разу - при том, что часть вакансий отдаёт 404.

    У живой вакансии есть описание, у архивной - дата архива. Нет ни того ни
    другого - страницы мы не видели.
    """
    if details is None:
        return True
    return not details.get("archived_at") and not details.get("description")


def verdict_for(find: dict, details: dict | None) -> dict:
    """Что записать в базу по ответу страницы.

    Три исхода: вакансия в архиве, страница не открылась, вакансия жива.
    Пустой словарь означает «ничего не менять».
    """
    now = datetime.now().isoformat(timespec="seconds")
    if page_missed(details):
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
    """Пройтись по протухшим карточкам и обновить их состояние.

    Результат каждой страницы пишется сразу. Раньше запись шла после всей
    пачки, и проверка VPN на последней карточке выбрасывала труд по всем
    предыдущим: 120 открытых страниц уходили впустую.
    """
    from scout import collector

    todo = stale_first(store.list_finds("analyzed"), limit=limit)
    if not todo:
        return {"checked": 0, "archived": 0, "gone": 0, "alive": 0}

    by_id = {find["id"]: find for find in todo}
    counts = {"checked": 0, "archived": 0, "gone": 0, "alive": 0}

    def save(vacancy_id: str, details: dict) -> None:
        find = by_id.get(vacancy_id)
        if find is None:
            return
        fields = verdict_for(find, details)
        if not fields:
            return
        store.update_find(vacancy_id, **fields)
        counts["checked"] += 1
        if fields.get("archived_at"):
            counts["archived"] += 1
        elif fields.get("status") == "dropped":
            counts["gone"] += 1
        else:
            counts["alive"] += 1

    try:
        collector.fetch_details(list(by_id), on_progress=on_progress, on_item=save)
    except Exception:
        # Прочитанное уже записано; остаток доберёт следующий заход.
        log.exception("перепроверка прервана, записано %s", counts["checked"])
        raise
    log.info("перепроверка: %s", counts)
    return counts


def stale_tail(finds: list[dict], now: datetime | None = None) -> list[dict]:
    """Хвост базы: разобранные находки старше окна выдачи.

    Закреплённые не трогаем - Иван поднял их руками. Подтверждённые живыми за
    последнюю неделю тоже: возраст находки у них большой, но страница открыта
    и прочитана.
    """
    now = now or datetime.now()
    tail = []
    for find in finds:
        if find.get("status") != "analyzed" or find.get("pinned_at"):
            continue
        found = _parsed(find.get("found_at"))
        if found is None or now - found <= STALE_AFTER:
            continue
        checked = _parsed(find.get("checked_at"))
        if checked is not None and now - checked <= ALIVE_TRUST:
            continue
        tail.append(find)
    return tail


def close_stale(dry_run: bool = True, limit: int = 0,
                now: datetime | None = None) -> dict:
    """Закрыть хвост по возрасту. По умолчанию ничего не пишет.

    Карточки уходят в «отсеянные» с причиной, по которой видно, что это
    возраст, а не отказ разведчика по сути вакансии.
    """
    now = now or datetime.now()
    tail = stale_tail(store.list_finds("analyzed"), now=now)
    if limit > 0:
        tail = tail[:limit]
    for find in tail:
        if dry_run:
            continue
        days = (now - _parsed(find["found_at"])).days
        store.update_find(find["id"], status="dropped",
                          drop_reason=f"устарела: найдена {days} дн. назад, "
                                      f"на hh не проверялась")
    return {"dry_run": dry_run, "closed": len(tail),
            "examples": [{"id": f["id"], "name": f.get("name"),
                          "found_at": f.get("found_at")} for f in tail[:10]]}


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
