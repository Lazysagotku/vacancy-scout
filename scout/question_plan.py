# -*- coding: utf-8 -*-
"""Консервативные черновики ответов на вопросы формы отклика.

Результат предназначен для просмотра человеком. Модуль не заполняет форму
и не отправляет отклик: часть анкет недоступна через официальный hh API.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scout.policy import classify_find, salary_line, salary_track_for_resume, select_mix

PORTFOLIO = "https://lazysagotku.github.io"


def plan_answers(find: dict, form: dict) -> list[dict]:
    """Предложить только ответы, прямо выводимые из подтверждённой политики."""
    # Сумма в анкете обязана совпадать с суммой в письме, а письмо пишется под
    # приложенное резюме. Трек политики берётся лишь когда резюме не выбрано.
    resume = find.get("resume") or ""
    track = salary_track_for_resume(resume) if resume else classify_find(find)
    planned: list[dict] = []
    raw_questions = (form or {}).get("questions") or []
    if not isinstance(raw_questions, list):
        return planned
    for number, item in enumerate(raw_questions[:20], start=1):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()[:400]
        # Второй рубеж против собственного виджета hh: карточки, разведанные
        # до 23.10, лежат в базе с этим «вопросом» внутри.
        if "вопрос работодателю" in question.casefold():
            continue
        kind = str(item.get("kind") or "").lower()
        lowered = question.casefold()
        suggestion = ""
        if kind in {"text", "input"}:
            salary_topic = any(word in lowered for word in ("зарплат", "доход", "вознагражден"))
            expectation = any(word in lowered for word in (
                "ожидан", "желаем", "хотел", "устраива", "рассматрива", "минимальн",
                "ориентир", "уровень дохода",
            ))
            past = any(word in lowered for word in ("предыдущ", "прошл", "бывш"))
            if track in {"python", "csharp", "support"} and salary_topic and expectation and not past:
                suggestion = salary_line(track)
            elif "портфолио" in lowered or "ссылку на проекты" in lowered:
                suggestion = PORTFOLIO
        raw_options = item.get("options") or []
        options = raw_options if isinstance(raw_options, list) else []
        planned.append({
            "number": number,
            "question": question,
            "kind": kind,
            "options": [str(option)[:120] for option in options[:20]],
            "required": bool(item.get("required")),
            "status": "suggested" if suggestion else "needs_user",
            "suggestion": suggestion,
        })
    return planned


def pending_questions(finds: list[dict], forms: dict[str, dict],
                      now: datetime | None = None, limit: int = 5) -> list[dict]:
    """Недавние анкеты для личного чата, без изменения состояния."""
    if not isinstance(forms, dict):
        return []
    now = now or datetime.now()
    result: list[dict] = []
    selected = select_mix([find for find in finds if find.get("status") == "analyzed"],
                          limit=30, now=now, max_age_days=3)
    for find in selected:
        form = forms.get(str(find.get("id"))) or {}
        if not isinstance(form, dict):
            continue
        if not form.get("questions") and not form.get("has_questions_flag"):
            continue
        try:
            checked = datetime.fromisoformat(form.get("checked_at") or "")
            if not timedelta(0) <= now - checked <= timedelta(hours=72):
                continue
        except (TypeError, ValueError):
            continue
        answers = plan_answers(find, form)
        if not answers:
            answers = [{"number": 1, "question": "hh сообщает о вопросах, но текст не удалось прочитать",
                        "kind": "unknown", "options": [], "required": True,
                        "status": "needs_user", "suggestion": ""}]
        result.append({"id": str(find["id"]), "name": str(find.get("name") or "")[:120],
                       "answers": answers})
        if len(result) >= limit:
            break
    return result
