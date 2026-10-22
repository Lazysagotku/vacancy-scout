# -*- coding: utf-8 -*-
"""Веб-интерфейс разведчика."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from scout import collector, runner, store, telegram_worker, tracker_link
from scout.analysis import analyze_find
from scout.application_plan import preview
from scout.demo import demo_vacancies
from scout.hh import Vacancy
from scout.letter import draft
from scout.policy import select_mix
from scout.scoring import score

app = FastAPI(title="Разведчик вакансий", version="2.0.0")

from scout.auth import BasicAuth

app.add_middleware(BasicAuth)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@app.on_event("startup")
def on_startup() -> None:
    store.init()
    runner.resume_if_enabled()
    telegram_worker.start_if_configured()


@app.on_event("shutdown")
def on_shutdown() -> None:
    telegram_worker.stop()


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
    recheck_limit: int | None = None
    auto_apply: bool | None = None
    live_apply: bool | None = None


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
    if payload.auto_apply is not None:
        store.set_setting("auto_apply", "1" if payload.auto_apply else "0")
    if payload.live_apply is not None:
        # Живая отправка - отдельный выключатель от автоматического круга:
        # круг можно гонять вхолостую и смотреть журнал, ничего не отправляя.
        store.set_setting("live_apply", "1" if payload.live_apply else "0")
    if payload.recheck_limit is not None:
        # 0 выключает перепроверку живости в цикле сбора.
        store.set_setting("recheck_limit", max(0, min(200, payload.recheck_limit)))
    return runner.status()


@app.post("/api/scout/start", tags=["скаутинг"])
def scout_start():
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


@app.get("/api/finds/curated", tags=["находки"])
def curated(limit: int = Query(default=30, ge=1, le=100)):
    """Короткая актуальная выдача с долями 60/30/10."""
    return select_mix(store.list_finds(), limit=limit)


@app.get("/api/applications/preview", tags=["отклики"])
def application_preview(limit: int = Query(default=30, ge=1, le=100)):
    """Показывает готовность кандидатов. Отклики не отправляет."""
    from scout import response_form
    items = preview(store.list_finds(), response_form.all_forms(), limit=limit)
    return {"mode": "dry-run", "items": items,
            "ready": sum(item["eligible"] for item in items)}


@app.post("/api/finds/recheck", tags=["находки"])
def recheck_finds(limit: int = Query(default=40, ge=1, le=200)):
    """Перепроверяет разобранные карточки: архив и страницы, которые не открываются."""
    return runner.recheck_once(limit)


@app.post("/api/applications/auto", tags=["отклики"])
def applications_auto(live: bool = Query(default=False)):
    """Один круг откликов. Без ?live=true ничего не отправляет."""
    from scout import apply, telegram_worker
    send_photo, send_message = telegram_worker.channel()
    return apply.auto_round(send_photo, send_message, dry_run=not live)


@app.get("/api/applications/ready", tags=["отклики"])
def applications_ready(limit: int = Query(default=25, ge=1, le=100)):
    """Кто готов к отправке прямо сейчас, с баллом и работодателем."""
    from scout import apply
    ready = apply.ready_now(limit)
    cards = {str(f.get("id")): f for f in store.list_finds()}
    out = []
    for item in ready:
        card = cards.get(str(item["id"]), {})
        out.append({"id": item["id"], "score": card.get("score"),
                    "name": card.get("name"), "employer": card.get("employer"),
                    "resume": card.get("resume"), "opinion": card.get("opinion"),
                    "url": card.get("url")})
    return {"ready": len(out), "daily_limit": apply.DAILY_LIMIT,
            "sent_today": store.sent_today(),
            "auto_apply": store.get_setting("auto_apply", "0") == "1",
            "live_apply": apply.live_allowed(), "items": out}


@app.post("/api/finds/close-stale", tags=["находки"])
def close_stale_finds(apply_changes: bool = Query(default=False, alias="apply")):
    """Закрывает хвост базы по возрасту. Без ?apply=true только показывает, сколько."""
    from scout import recheck
    return recheck.close_stale(dry_run=not apply_changes)


@app.post("/api/finds/refresh-resumes", tags=["находки"])
def refresh_resumes(apply_changes: bool = Query(default=False)):
    """Пересчитывает приложенное резюме по текущим правилам. Без флага - только показывает."""
    from scout import recheck
    return recheck.refresh_resumes(dry_run=not apply_changes)


@app.post("/api/forms/peek", tags=["отклики"])
def peek_response_forms(limit: int = Query(default=8, ge=1, le=30)):
    """Разведка форм отклика у лучших карточек. Отклик не отправляется.

    Делается отдельной командой, а не в каждом проходе сбора: открытие формы -
    это клик по кнопке отклика на hh, и делать его сотни раз подряд незачем.
    """
    from scout.analysis import peek_forms
    checked = peek_forms(store.list_finds("analyzed"), limit=limit)
    return {"checked": checked, "sent": False}


@app.get("/api/applications", tags=["отклики"])
def application_log(limit: int = Query(default=100, ge=1, le=500)):
    """Журнал решений по откликам, включая заблокированные и неудачные."""
    return store.applications(limit)


@app.post("/api/applications/send", tags=["отклики"])
def send_applications(limit: int = Query(default=5, ge=1, le=20),
                      live: bool = Query(default=False)):
    """Отправка откликов. Живой режим требует и флага, и настройки live_apply=1."""
    from scout import apply
    return apply.apply_batch(limit=limit, dry_run=not live)


@app.post("/api/letters/prepare", tags=["находки"])
def prepare_letters(limit: int = Query(default=10, ge=1, le=30)):
    """Готовит письма отдельной AI-фазой, когда Claude доступен через VPN."""
    return runner.prepare_letters(limit)


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
    # Нажатие - сигнал интереса: карточка закрепляется сверху и держится там
    store.update_find(vacancy_id, pinned_at=datetime.now().isoformat(timespec="seconds"))
    updated = write_letter(find)
    if not updated:
        raise HTTPException(502, "Claude не вернул письмо, попробуй ещё раз")
    return {"letter": updated["letter"], "review": updated.get("review") or "", "letter_kind": "claude",
            "opinion": updated.get("opinion") or ""}


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
