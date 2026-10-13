# -*- coding: utf-8 -*-
"""Фоновый скаутинг: сам ходит за вакансиями с заданным интервалом.

Состояние держится в одном месте и всегда доступно снаружи - без этого
непонятно, работает ли скаут вообще, а молчание легко перепутать
с «вакансий нет».
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from scout import collector, store
from scout.collector import CollectError, collect
from scout.search_sources import built_in_sources

log = logging.getLogger(__name__)


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
    last_forms: int = 0            # у скольких снял вопросы формы отклика
    last_error: str | None = None
    last_note: str | None = None   # не ошибка, но человеку знать надо: сессия hh, пропущенная строка
    authorized: bool | None = None # жива ли сессия hh у браузера скаута
    interval_minutes: int = 180
    search_url: str = ""
    preparing: bool = False
    last_prepared: int = 0
    last_prepare_error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


_state = State()
_lock = threading.Lock()
_thread: threading.Thread | None = None
_prep_thread: threading.Thread | None = None
_wake = threading.Event()


def status() -> dict:
    with _lock:
        _state.interval_minutes = int(store.get_setting("interval_minutes", "180"))
        _state.search_url = store.get_setting("search_url", "")
        return _state.as_dict()


def scan_once() -> dict:
    """Один проход сбора. Возвращает результат и обновляет состояние."""
    with _lock:
        if _state.busy or _state.preparing:
            return {"skipped": "сбор или подготовка писем уже идёт"}
        _state.busy = True

    run_id = store.start_run()
    url = store.get_setting("search_url", "")
    found, fresh, error, analyzed = 0, 0, "", 0
    notes: list[str] = []
    try:
        with _lock:
            _state.stage = "проверка входа"
        # Подборка «по резюме» без сессии превращается в общий поиск, и в
        # списке появляется что угодно. Проверяем до сбора и говорим прямо.
        try:
            authorized = collector.is_authorized()
        except Exception:
            authorized = None
        with _lock:
            _state.authorized = authorized
        if authorized is False:
            notes.append("Сессия hh истекла - нажми «Войти в hh», иначе подборка по резюме отдаёт общий поиск.")

        with _lock:
            _state.stage = "сбор"
        # Ссылок может быть несколько, по одной на строку. Подборка hh «по резюме» -
        # рекомендательная выдача, и она пропускала вакансии, которые Иван находил
        # обычным поиском по словам (11.09: восемь вакансий, ни одной в базе).
        # Поэтому рядом с подборкой идут поисковые запросы по ключевым словам.
        # Одна плохая строка не должна ронять весь прогон.
        known = store.known_ids()
        seen: dict[str, dict] = {}
        lines = [line.strip() for line in url.splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        # Сохранённая подборка hh может истечь. Поиск по словам идёт всегда,
        # поэтому одна ссылка с 404 не превращается в пустую выдачу.
        sources = [("сохранённая подборка", line, 2) for line in lines]
        sources.extend((source.title, source.url, 1) for source in built_in_sources())
        worked = 0
        for number, (label, line, pages) in enumerate(sources, start=1):
            with _lock:
                _state.stage = f"сбор {number} из {len(sources)}"
            try:
                for item in collect(line, pages=pages, known=known):
                    item.setdefault("query", label)
                    seen.setdefault(item["id"], item)
                worked += 1
            except CollectError as problem:
                notes.append(f"строка {number}: {problem}")
                if isinstance(problem, collector.VpnCheck):
                    break                                  # дальше тот же ответ
        # Ни одна ссылка не открылась, но сессия есть - главная hh под
        # авторизацией тоже показывает подборку. Просьба Ивана 12.09.
        if not worked and sources and authorized:
            with _lock:
                _state.stage = "сбор с главной hh"
            try:
                for item in collect("https://hh.ru/", known=known):
                    seen.setdefault(item["id"], item)
                notes.append("Ссылки из настроек не открылись - взял вакансии с главной hh.")
            except CollectError as problem:
                notes.append(f"главная hh: {problem}")
        if not worked and not seen:
            error = notes[-1] if notes else "Не удалось открыть ни одной ссылки."

        items = list(seen.values())
        found = len(items)
        # Отсеянные остаются в базе, поэтому save_finds их просто не тронет -
        # повторно они в «новые» не попадут
        fresh = store.save_finds(items, query="смешанный поиск")

        # Разбираем сразу: человеку нужен готовый список, а не очередь на 200 кнопок
        limit = int(store.get_setting("analyze_limit", "120"))
        if limit:
            analyzed = analyze_batch(limit)
    except Exception as problem:                       # неожиданное - тоже показываем
        error = collector.short_error(problem)
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
            _state.last_note = " ".join(notes) or None
            if _state.running:
                minutes = int(store.get_setting("interval_minutes", "180"))
                _state.next_run = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return {"found": found, "fresh": fresh, "analyzed": analyzed, "error": error or None,
            "note": " ".join(notes) or None}


def analyze_batch(limit: int = 120) -> int:
    """Открывает неразобранные находки и разбирает их в одном браузере.

    Открываем всех, кто прошёл стоп-лист по заголовку: по названию не угадать,
    что внутри, а страница стоит секунды и ноль токенов. Потолок на прогон -
    из-за hh: сотни страниц подряд с аккаунта Ивана - это капча на его
    аккаунте. Письма - лучшим по оценке, в пределах бюджета на прогон.
    """
    from scout.analysis import analyze_many          # импорт здесь: избегаем цикла

    batch = [f for f in store.list_finds("new") if f.get("verdict") != "пропустить"][:limit]
    if not batch:
        return 0
    def progress(done: int, total: int, stage: str = "разбор") -> None:
        with _lock:
            _state.stage = f"{stage} {done} из {total}"

    try:
        # Браузерная фаза должна завершаться без обращения к Claude: hh может
        # работать лишь при выключенном VPN. Письма готовятся отдельным запуском.
        result = analyze_many(batch, on_progress=progress, letter_budget=0)

        with _lock:
            # Открытие формы через браузер может менять поведение на hh.
            # Автоматически эту операцию не выполняем.
            _state.last_forms = 0
            _state.last_analyzed = result
            # Прошлая ошибка больше не актуальна: раз описания пришли,
            # держать на виду старую жалобу на VPN - врать человеку
            if result:
                _state.last_error = None
        return result
    except CollectError as problem:
        # Причину надо показать: молчаливый ноль выглядит как «нечего разбирать»
        with _lock:
            _state.last_error = collector.short_error(problem)
        return 0
    except Exception as problem:
        # Внутренняя ошибка кода - не сетевая. Раньше она проходила через
        # short_error и человек читал совет «проверь маршрут к hh», хотя hh
        # отвечал нормально. Текст исключения в состояние не выносим: он может
        # содержать фрагмент вакансии или путь на диске.
        log.exception("Разбор пачки прерван внутренней ошибкой")
        with _lock:
            _state.last_error = f"Внутренняя ошибка разбора: {type(problem).__name__}. Смотри лог сервиса."
        return 0
    finally:
        with _lock:
            _state.stage = ""


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
        already_running = _state.running
        if not already_running:
            _state.running = True
            _state.next_run = datetime.now().isoformat(timespec="seconds")
    if already_running:
        return status()
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


def _prepare_letters_run(limit: int) -> None:
    from scout.analysis import catch_up_letters

    count = 0
    error = None
    try:
        count = catch_up_letters(limit)
    except Exception as problem:
        # Ошибка стороннего CLI может содержать личные данные или пути.
        error = type(problem).__name__
        log.error("Сбой подготовки писем: %s", error)
    finally:
        with _lock:
            _state.preparing = False
            _state.last_prepared = count
            _state.last_prepare_error = error


def prepare_letters(limit: int = 10) -> dict:
    """Запустить AI-фазу отдельно от браузерного сбора, без откликов."""
    global _prep_thread
    if not 1 <= limit <= 30:
        raise ValueError("Лимит писем должен быть от 1 до 30")
    with _lock:
        if _state.busy or _state.preparing:
            return {"started": False, "reason": "Сбор или подготовка уже идёт"}
        _state.preparing = True
        _state.last_prepare_error = None
    from scout import writer
    try:
        available = writer.available()
    except Exception:
        available = False
    if not available:
        with _lock:
            _state.preparing = False
        return {"started": False, "reason": "Claude недоступен - проверь VPN"}
    try:
        _prep_thread = threading.Thread(target=_prepare_letters_run, args=(limit,),
                                        daemon=True, name="scout-letter-preparation")
        _prep_thread.start()
    except Exception:
        with _lock:
            _state.preparing = False
        raise
    return {"started": True, "limit": limit}


_login_thread: threading.Thread | None = None


def start_login(send_photo, send_message) -> dict:
    """Запустить вход в hh в отдельном потоке: он ждёт ответа человека.

    Опрос Telegram не должен стоять, пока браузер ждёт код, поэтому вход живёт
    своим потоком, а ответ приходит через `hh_login.deliver`.
    """
    global _login_thread
    from scout import hh_login

    if _login_thread and _login_thread.is_alive():
        return {"started": False, "note": "Вход уже идёт"}

    def run() -> None:
        try:
            hh_login.login(send_photo, send_message)
        except Exception as problem:
            log.error("Вход в hh прерван: %s", type(problem).__name__)
            try:
                send_message(f"Вход прерван: {type(problem).__name__}")
            except Exception:
                pass

    _login_thread = threading.Thread(target=run, daemon=True, name="scout-hh-login")
    _login_thread.start()
    return {"started": True}


def recheck_once(limit: int = 40) -> dict:
    """Перепроверка разобранных карточек: архив и недоступные страницы."""
    from scout import recheck

    with _lock:
        if _state.busy or _state.preparing:
            return {"skipped": "сбор или подготовка уже идёт"}
        _state.busy = True
    try:
        def progress(done: int, total: int, stage: str = "перепроверка") -> None:
            with _lock:
                _state.stage = f"{stage} {done} из {total}"
        return recheck.recheck(limit=limit, on_progress=progress)
    finally:
        with _lock:
            _state.busy = False
            _state.stage = ""
