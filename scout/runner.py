# -*- coding: utf-8 -*-
"""Фоновый скаутинг: сам ходит за вакансиями с заданным интервалом.

Состояние держится в одном месте и всегда доступно снаружи - без этого
непонятно, работает ли скаут вообще, а молчание легко перепутать
с «вакансий нет».
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from scout import store
from scout.collector import CollectError, collect


@dataclass
class State:
    running: bool = False          # включён ли цикл
    busy: bool = False             # идёт ли сбор прямо сейчас
    stage: str = ""                # что делает сейчас: сбор или разбор
    last_run: str | None = None
    next_run: str | None = None
    last_found: int = 0
    last_fresh: int = 0
    last_analyzed: int = 0         # сколько разобрал в последнем проходе
    last_error: str | None = None
    interval_minutes: int = 180
    search_url: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


_state = State()
_lock = threading.Lock()
_thread: threading.Thread | None = None
_wake = threading.Event()


def status() -> dict:
    with _lock:
        _state.interval_minutes = int(store.get_setting("interval_minutes", "180"))
        _state.search_url = store.get_setting("search_url", "")
        return _state.as_dict()


def scan_once() -> dict:
    """Один проход сбора. Возвращает результат и обновляет состояние."""
    with _lock:
        if _state.busy:
            return {"skipped": "сбор уже идёт"}
        _state.busy = True

    run_id = store.start_run()
    url = store.get_setting("search_url", "")
    found, fresh, error, analyzed = 0, 0, "", 0
    try:
        with _lock:
            _state.stage = "сбор"
        items = collect(url)
        found = len(items)
        # Отсеянные остаются в базе, поэтому save_finds их просто не тронет -
        # повторно они в «новые» не попадут
        fresh = store.save_finds(items, query=url)

        # Разбираем сразу: человеку нужен готовый список, а не очередь на 200 кнопок
        limit = int(store.get_setting("analyze_limit", "15"))
        if limit:
            with _lock:
                _state.stage = "разбор"
            analyzed = analyze_batch(limit)
    except CollectError as problem:
        error = str(problem)
    except Exception as problem:                       # неожиданное - тоже показываем
        error = f"Непредвиденная ошибка: {problem}"
    finally:
        store.finish_run(run_id, found, fresh, error)
        now = datetime.now()
        with _lock:
            _state.busy = False
            _state.stage = ""
            _state.last_analyzed = analyzed
            _state.last_run = now.isoformat(timespec="seconds")
            _state.last_found = found
            _state.last_fresh = fresh
            _state.last_error = error or None
            if _state.running:
                minutes = int(store.get_setting("interval_minutes", "180"))
                _state.next_run = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return {"found": found, "fresh": fresh, "analyzed": analyzed, "error": error or None}


def analyze_batch(limit: int = 15) -> int:
    """Разбирает лучшие неразобранные находки в одном браузере.

    Список отсортирован по предварительной оценке, поэтому первыми уходят
    в разбор те, у кого больше шансов. Совсем безнадёжные не открываем:
    их вердикт понятен по заголовку, а каждая страница стоит секунд.
    """
    from scout.analysis import analyze_many          # импорт здесь: избегаем цикла

    batch = [f for f in store.list_finds("new")[:limit * 2]
             if (f.get("score") or 0) >= 20][:limit]
    if not batch:
        return 0
    try:
        return analyze_many(batch)
    except Exception as problem:
        # Причину надо показать: молчаливый ноль выглядит как «нечего разбирать»
        with _lock:
            _state.last_error = str(problem)
        return 0


def _loop() -> None:
    while True:
        with _lock:
            if not _state.running:
                return
        scan_once()
        minutes = int(store.get_setting("interval_minutes", "180"))
        # Ждём с прерыванием: остановка не должна ждать конца интервала
        if _wake.wait(timeout=minutes * 60):
            _wake.clear()


def start() -> dict:
    global _thread
    with _lock:
        if _state.running:
            return status()
        _state.running = True
        _state.next_run = datetime.now().isoformat(timespec="seconds")
    store.set_setting("enabled", "1")
    _wake.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="scout-runner")
    _thread.start()
    return status()


def stop() -> dict:
    with _lock:
        _state.running = False
        _state.next_run = None
    store.set_setting("enabled", "0")
    _wake.set()
    return status()


def resume_if_enabled() -> None:
    """После перезапуска приложения возвращаем скаут в то же состояние."""
    if store.get_setting("enabled", "0") == "1":
        start()
