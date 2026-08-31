# -*- coding: utf-8 -*-
"""Грубая оценка находки по заголовку и условиям.

Нужна, чтобы не заходить в каждую из сотни вакансий. Точности тут не ждём:
задача - развести список на «стоит смотреть» и «мусор», а решает всё равно
полный разбор.
"""

from __future__ import annotations

from scout import profile

# Названия, по которым видно, что роль не наша - без чтения описания
STOP = [
    ("продаж", "продажи, а не инженерная роль"),
    ("оператор", "операторская роль"),
    ("курьер", "не ИТ-роль"),
    ("менеджер по работе с клиент", "клиентский менеджмент"),
    ("стажер", "стажировка"),
    ("стажёр", "стажировка"),
    ("intern", "стажировка"),
    ("junior", "junior-позиция"),
    ("младший", "junior-позиция"),
    ("1с", "1С-специализация"),
    ("1c", "1С-специализация"),
    ("helpdesk", "первая линия"),
    ("help desk", "первая линия"),
    ("первой линии", "первая линия"),
    ("первая линия", "первая линия"),
    ("1l", "первая линия"),
    ("битрикс", "Битрикс-специализация"),
    ("devops", "DevOps: пробел Kubernetes и IaC"),
    ("sre", "SRE: пробел Kubernetes и IaC"),
    ("с выездами", "выездная работа"),
    ("machine learning", "обучение моделей"),
    ("data scientist", "обучение моделей"),
]

# Слова в названии, которые говорят, что роль по профилю
GOOD = {
    "сопровожден": 30, "поддержк": 25, "эксплуатац": 25, "инженер": 15,
    "python": 25, "backend": 20, "бэкенд": 20, "c#": 25, ".net": 25,
    "postgres": 25, "баз данных": 20, "мониторинг": 25, "интеграц": 20,
    "автоматизац": 20, "данных": 12, "ai": 18, "ии": 12, "инцидент": 22,
    "ведущий": 10, "старший": 10,
}


def prescore(item: dict) -> dict:
    """Возвращает оценку, вердикт и заметки по одной находке."""
    name = (item.get("name") or "").lower()
    notes: list[str] = []

    for word, reason in STOP:
        if word in name:
            return {"score": 0, "verdict": "пропустить", "notes": reason}

    value = sum(weight for word, weight in GOOD.items() if word in name)
    value = min(value, 85)                       # потолок: без описания выше не судим

    top = item.get("salary_to") or item.get("salary_from")
    if top and top < profile.SALARY_FLOOR:
        value = max(0, value - 25)
        notes.append(f"вилка ниже планки: {top // 1000}к")
    elif not top:
        notes.append("вилка не указана")

    if item.get("remote"):
        value += 5
        notes.append("удалёнка")

    if "Без опыта" in (item.get("experience") or ""):
        value = max(0, value - 15)
        notes.append("роль рассчитана на новичка")

    value = max(0, min(100, value))
    verdict = ("сильный матч" if value >= 60 else
               "стоит посмотреть" if value >= 35 else "слабый матч")
    return {"score": value, "verdict": verdict, "notes": "; ".join(notes)}
