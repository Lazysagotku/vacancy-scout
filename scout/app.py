# -*- coding: utf-8 -*-
"""Веб-интерфейс разведчика."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from scout import collector, runner, store, tracker_link
from scout.analysis import analyze_find
from scout.demo import demo_vacancies
from scout.hh import Vacancy
from scout.letter import draft
from scout.scoring import score

app = FastAPI(title="Разведчик вакансий", version="2.0.0")

from scout.auth import BasicAuth

app.add_middleware(BasicAuth)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@app.on_event("startup")
def on_startup() -> None:
    store.init()
    runner.resume_if_enabled()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health", tags=["служебное"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Скаутинг ---

@app.get("/api/status", tags=["скаутинг"])
def get_status():
    """Состояние скаута: работает ли, когда собирал, что нашёл."""
    return {"scout": runner.status(), "counts": store.counts(), "runs": store.last_runs(3)}


class Settings(BaseModel):
    search_url: str | None = None
    interval_minutes: int | None = None
    analyze_limit: int | None = None
    letter_limit: int | None = None


@app.post("/api/settings", tags=["скаутинг"])
def save_settings(payload: Settings):
    if payload.search_url is not None:
        store.set_setting("search_url", payload.search_url.strip())
    if payload.interval_minutes is not None:
        store.set_setting("interval_minutes", max(15, payload.interval_minutes))
    if payload.analyze_limit is not None:
        store.set_setting("analyze_limit", max(0, min(300, payload.analyze_limit)))
    if payload.letter_limit is not None:
        store.set_setting("letter_limit", max(0, min(100, payload.letter_limit)))
    return runner.status()


@app.post("/api/scout/start", tags=["скаутинг"])
def scout_start():
    if not store.get_setting("search_url"):
        raise HTTPException(400, "Сначала укажите ссылку на выдачу hh")
    return runner.start()


@app.post("/api/scout/stop", tags=["скаутинг"])
def scout_stop():
    return runner.stop()


@app.post("/api/scout/login", tags=["скаутинг"])
def scout_login():
    """Открывает браузер Скаута для входа в hh. Вход нужен один раз."""
    import threading
    threading.Thread(target=collector.open_login, daemon=True).start()
    return {"opened": True,
            "hint": "Откроется окно браузера - войдите в hh и закройте его. "
                    "Профиль сохранится, повторно входить не потребуется."}


@app.get("/api/scout/auth", tags=["скаутинг"])
def scout_auth():
    """Проверяет, авторизован ли браузер Скаута на hh."""
    try:
        return {"authorized": collector.is_authorized()}
    except Exception as error:
        return {"authorized": False, "error": str(error)}


@app.post("/api/scout/scan", tags=["скаутинг"])
def scout_scan():
    """Один проход прямо сейчас, не дожидаясь интервала."""
    return runner.scan_once()


# --- Находки и разбор ---

@app.get("/api/finds", tags=["находки"])
def get_finds(status: str | None = Query(default=None)):
    return store.list_finds(status)


@app.post("/api/finds/{vacancy_id}/analyze", tags=["находки"])
def analyze(vacancy_id: str, deep: bool = Query(default=True)):
    """Разбирает находку: догружает описание, скорит, готовит черновик письма."""
    try:
        return analyze_find(vacancy_id, deep=deep)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except collector.CollectError as error:
        raise HTTPException(502, str(error)) from error


@app.post("/api/finds/{vacancy_id}/rewrite", tags=["находки"])
def rewrite(vacancy_id: str):
    """Письмо и мнение под вакансию через Claude - по кнопке в карточке."""
    from scout import writer
    from scout.analysis import write_letter
    find = store.get_find(vacancy_id)
    if not find:
        raise HTTPException(404, "Находка не найдена")
    if not (find.get("description") or ""):
        raise HTTPException(409, "Сначала разбор: без описания вакансии письмо не написать")
    if not writer.available():
        raise HTTPException(503, "Claude недоступен: проверь VPN")
    updated = write_letter(find)
    if not updated:
        raise HTTPException(502, "Claude не вернул письмо, попробуй ещё раз")
    return {"letter": updated["letter"], "review": updated.get("review") or "", "letter_kind": "claude"}


class Added(BaseModel):
    url: str


@app.post("/api/finds/add", tags=["находки"])
def add_find(payload: Added):
    """Вакансия по ссылке или id - минуя выдачу.

    Для тех, что Иван находит сам: раньше он присылал текст в чат, теперь
    ссылку сюда. Разведчик открывает страницу, оценивает, пишет письмо
    и мнение - в чате ничего держать не нужно.
    """
    import re
    from scout.analysis import analyze_find
    m = re.search(r"(\d{6,})", payload.url or "")
    if not m:
        raise HTTPException(400, "Не вижу id вакансии в ссылке")
    vacancy_id = m.group(1)
    find = store.get_find(vacancy_id)
    if not find:
        store.save_finds([{"id": vacancy_id, "name": f"вакансия {vacancy_id}",
                           "url": f"https://hh.ru/vacancy/{vacancy_id}", "employer": ""}],
                         query="вручную")
        find = store.get_find(vacancy_id)
    elif find.get("status") == "dropped":
        store.update_find(vacancy_id, status="new", drop_reason=None)
    try:
        return analyze_find(vacancy_id, deep=True)
    except collector.CollectError as error:
        raise HTTPException(502, str(error)) from error


@app.get("/api/finds/{vacancy_id}/brief", tags=["находки"])
def get_brief(vacancy_id: str):
    """Материал для письма: разделы вакансии, зацепки, требования.

    Письмо по этому материалу пишет человек. Шаблонная сборка из списка
    навыков давала одинаковые письма, потому что теряла сам текст вакансии.
    """
    from scout import brief
    items = [f for f in store.list_finds() if f["id"] == vacancy_id]
    if not items:
        raise HTTPException(404, "Находка не найдена")
    return {"text": brief.as_text(items[0]), **brief.build(items[0])}


@app.get("/api/finds/{vacancy_id}/form", tags=["находки"])
def get_form(vacancy_id: str):
    """Отдаёт уже снятые вопросы формы отклика, если они есть."""
    from scout import response_form
    return response_form.known(vacancy_id) or {"id": vacancy_id, "questions": [], "note": "не проверялось"}


@app.post("/api/finds/{vacancy_id}/form", tags=["находки"])
def peek_form(vacancy_id: str):
    """Открывает форму отклика и снимает вопросы работодателя.

    Отклик не отправляется: скрипт доходит до формы, читает поля и уходит.
    """
    from scout import response_form
    from scout.analysis import analyze_find
    try:
        data = response_form.peek(vacancy_id)
        # Письмо переписываем сразу: теперь известно, о чём спросят формой,
        # и дублировать это в тексте не нужно.
        if data.get("questions"):
            try:
                analyze_find(vacancy_id, deep=False)
            except Exception:
                pass          # разбор не критичен, вопросы уже сняты
        return data
    except collector.CollectError as error:
        raise HTTPException(502, str(error)) from error


@app.post("/api/finds/analyze-all", tags=["находки"])
def analyze_all(limit: int = Query(default=120, ge=1, le=400)):
    """Разбирает лучшие неразобранные прямо сейчас, не дожидаясь сбора."""
    import threading
    threading.Thread(target=runner.analyze_batch, args=(limit,), daemon=True).start()
    return {"started": True, "limit": limit,
            "hint": "Разбор идёт в фоне, список обновляется по мере готовности"}


class Mark(BaseModel):
    status: str
    reason: str | None = None


@app.post("/api/finds/{vacancy_id}/status", tags=["находки"])
def set_status(vacancy_id: str, payload: Mark):
    """Смена статуса. По «sent» вакансия переезжает в трекер."""
    if payload.status not in {"new", "analyzed", "sent", "dropped"}:
        raise HTTPException(400, "Неизвестный статус")

    items = [f for f in store.list_finds() if f["id"] == vacancy_id]
    if not items:
        raise HTTPException(404, "Находка не найдена")
    find = items[0]

    if payload.status == "sent":
        try:
            tracker_id = tracker_link.push(find)
        except tracker_link.TrackerUnavailable as error:
            raise HTTPException(502, str(error)) from error
        store.update_find(vacancy_id, status="tracked",
                          sent_at=datetime.now().isoformat(timespec="seconds"))
        return {"moved_to_tracker": tracker_id, "status": "tracked"}

    if payload.status == "dropped":
        store.drop(vacancy_id, payload.reason or "")
        return {"status": "dropped", "reason": payload.reason or "не заинтересовала"}

    store.update_find(vacancy_id, status=payload.status, drop_reason=None)
    return {"status": payload.status}


# --- Демо: показать работу без hh ---

@app.get("/api/demo", tags=["демо"])
def demo():
    items = []
    for vacancy in demo_vacancies():
        verdict = score(vacancy)
        items.append({
            "id": vacancy.id, "name": vacancy.name, "employer": vacancy.employer,
            "url": vacancy.url, "salary": vacancy.salary_text, "experience": vacancy.experience,
            "score": verdict.score, "verdict": verdict.verdict, "css": verdict.css,
            "track": verdict.track,
            "matched": ", ".join(n for n, _ in verdict.matched),
            "gaps": "; ".join(verdict.gaps), "blockers": "; ".join(verdict.blockers),
            "notes": "; ".join(verdict.notes), "letter": draft(vacancy, verdict),
        })
    items.sort(key=lambda item: -item["score"])
    return items
