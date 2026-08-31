# -*- coding: utf-8 -*-
"""Веб-интерфейс разведчика."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from scout import hh
from scout.demo import demo_vacancies
from scout.letter import draft
from scout.scoring import score

app = FastAPI(title="Разведчик вакансий", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health", tags=["служебное"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scan", tags=["поиск"])
def scan(
    q: str = Query(default="инженер сопровождения"),
    source: str = Query(default="demo", pattern="^(demo|hh)$"),
    limit: int = Query(default=12, ge=1, le=50),
):
    """Ищет вакансии и оценивает каждую по профилю."""
    warning = ""
    if source == "hh":
        try:
            found = hh.search(q, per_page=limit)
            for vacancy in found:
                hh.enrich(vacancy)
        except hh.NeedsToken as error:
            warning = f"{error} Показан демо-набор."
            found = demo_vacancies()
        except Exception as error:                      # сеть, таймаут, смена формата
            warning = f"hh недоступен ({error}). Показан демо-набор."
            found = demo_vacancies()
    else:
        found = demo_vacancies()

    items = []
    for vacancy in found:
        verdict = score(vacancy)
        items.append({
            "id": vacancy.id,
            "name": vacancy.name,
            "employer": vacancy.employer,
            "url": vacancy.url,
            "salary": vacancy.salary_text,
            "schedule": vacancy.schedule,
            "experience": vacancy.experience,
            "published": vacancy.published,
            "score": verdict.score,
            "verdict": verdict.verdict,
            "css": verdict.css,
            "track": verdict.track,
            "matched": [name for name, _ in verdict.matched],
            "gaps": verdict.gaps,
            "blockers": verdict.blockers,
            "notes": verdict.notes,
            "letter": draft(vacancy, verdict),
        })

    items.sort(key=lambda item: -item["score"])
    return {
        "warning": warning,
        "summary": {
            "total": len(items),
            "strong": sum(1 for i in items if i["verdict"] == "сильный матч"),
            "maybe": sum(1 for i in items if i["verdict"] == "стоит посмотреть"),
            "skipped": sum(1 for i in items if i["verdict"] == "пропустить"),
        },
        "items": items,
    }
