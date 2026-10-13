# -*- coding: utf-8 -*-
"""Безопасный dry-run отбора откликов. Сетевых вызовов и отправки здесь нет."""

from __future__ import annotations

from datetime import datetime, timedelta

from scout import profile
from scout.policy import (classify_find, csharp_experience_ok, salary_line,
                          salary_track_for_resume, select_mix)
from scout.writer import _looks_like_letter

MAX_SEND_AGE = timedelta(hours=72)


def _recent(value: str | None, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(value or "")
    except ValueError:
        return False
    return timedelta(0) <= now - parsed <= MAX_SEND_AGE


def evaluate(find: dict, form: dict | None = None, now: datetime | None = None) -> dict:
    """Вернуть причины блокировки для одной вакансии до любой внешней отправки."""
    now = now or datetime.now()
    track = classify_find(find)
    reasons: list[str] = []
    if find.get("status") in {"sent", "tracked"} or find.get("sent_at"):
        reasons.append("Отклик уже отмечен")
    elif find.get("status") != "analyzed":
        reasons.append("Вакансия не разобрана")
    if not _recent(find.get("found_at"), now):
        reasons.append("Вакансия старше 72 часов")
    if not _recent(find.get("analyzed_at"), now):
        reasons.append("Разбор вакансии устарел")
    if find.get("archived_at"):
        reasons.append("Вакансия в архиве")
    if (find.get("score") or 0) < 70 or find.get("verdict") != "сильный матч":
        reasons.append("Совпадение недостаточно сильное")
    if (find.get("opinion") or "") != "Рекомендую" or find.get("blockers"):
        reasons.append("Положительное мнение без блокеров отсутствует")
    # Резюме - источник правды для зарплаты и проверки стажа. Трек политики
    # отвечает за доли выдачи и совпадает с резюме не всегда: на 4000 находок
    # 32 расхождения («Technical support engineer» с Python-резюме). Проверять
    # письмо по треку политики означало блокировать такие отклики навсегда.
    resume = find.get("resume") or ""
    if resume not in profile.RESUMES.values():
        reasons.append("Резюме для отклика не выбрано")
        resume_track = None
    else:
        resume_track = salary_track_for_resume(resume)
    if resume_track == "csharp" and not csharp_experience_ok(find.get("experience")):
        reasons.append("C# требует опыт вне 1-3 лет")
    if not find.get("description"):
        reasons.append("Описание вакансии отсутствует")
    letter = find.get("letter") or ""
    if find.get("letter_kind") != "claude" or not _looks_like_letter(letter):
        reasons.append("Письмо не проверено моделью")
    if resume_track and salary_line(resume_track) not in letter:
        reasons.append("Нет согласованной зарплаты в письме")
    if form is None or not _recent(form.get("checked_at"), now):
        reasons.append("Форма отклика не проверена")
    else:
        # Анкета с вопросами больше не блокирует отклик целиком: известные
        # ответы берутся из политики (зарплата по резюме, портфолио). Блокируют
        # только вопросы, ответа на которые в фактах нет - отвечать за человека
        # наугад нельзя, ответ уходит работодателю от его имени.
        from scout.question_plan import plan_answers
        unknown = [item for item in plan_answers(find, form)
                   if item.get("status") != "suggested"]
        if unknown:
            asked = (unknown[0].get("question") or "").strip()
            reasons.append("Форма требует ответа: " + asked[:60] if asked
                           else "Форма требует ответа")
        elif form.get("has_questions_flag") and not form.get("questions"):
            # hh сказал, что вопросы есть, а прочитать их не удалось.
            reasons.append("Форма требует ответа: вопросы не прочитаны")
        if not form.get("has_letter"):
            reasons.append("Поле письма не подтверждено")
        if form.get("note") and form["note"] != "Дополнительных вопросов нет: только выбор резюме и сопроводительное письмо":
            reasons.append("Форма сообщает о проблеме")
    return {"id": find.get("id"), "track": track, "reasons": reasons,
            "eligible": not reasons, "sent": False}


def preview(finds: list[dict], forms: dict[str, dict] | None = None,
            now: datetime | None = None, limit: int = 30) -> list[dict]:
    """Доли 60/30/10 для dry-run, без отправки и изменения БД."""
    now = now or datetime.now()
    forms = forms or {}
    chosen = select_mix([find for find in finds if find.get("status") == "analyzed"],
                        limit=limit, now=now, max_age_days=3)
    return [evaluate(find, forms.get(str(find.get("id"))), now) for find in chosen]
