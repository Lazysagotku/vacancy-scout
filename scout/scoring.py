# -*- coding: utf-8 -*-
"""Оценка вакансии по профилю.

Оценка нужна не ради цифры, а ради объяснения: что совпало, чего не хватает
и что честно сказать в письме. Поэтому вместе с баллом всегда возвращаются
причины - без них скоринг бесполезен.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import re

from scout import profile
from scout.hh import Vacancy


@dataclass
class Verdict:
    score: int                                  # 0-100
    track: str | None
    matched: list[tuple[str, str]] = field(default_factory=list)   # навык, подтверждение
    gaps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.blockers:
            return "пропустить"
        if self.score >= 70:
            return "сильный матч"
        if self.score >= 45:
            return "стоит посмотреть"
        return "слабый матч"

    @property
    def css(self) -> str:
        return {"пропустить": "skip", "сильный матч": "strong",
                "стоит посмотреть": "maybe", "слабый матч": "weak"}[self.verdict]


# Короткие ключи нельзя искать подстрокой: «ии» сидит внутри «организации»
# и «информации», из-за чего вакансия сопровождения уезжала в ai-трек.
_SHORT = {"ai", "ии", "l2", "l3", "c#"}


def _hit(word: str, text: str) -> bool:
    if len(word) > 3 and word not in _SHORT:
        return word in text
    return re.search(r"(?<![\w#.])" + re.escape(word) + r"(?![\w#])", text) is not None


def detect_track(text: str, title: str = "") -> str | None:
    """Определяет трек по заголовку и описанию.

    Заголовок весит втрое: «Инженер сопровождения» говорит о роли больше,
    чем случайное упоминание нейросетей в списке технологий.
    """
    low, head = text.lower(), (title or "").lower()
    best, best_score = None, 0
    for track, words in profile.TRACKS.items():
        value = sum(1 for w in words if _hit(w, low))
        value += 3 * sum(1 for w in words if _hit(w, head))
        if value > best_score:
            best, best_score = track, value
    return best


def score(vacancy: Vacancy) -> Verdict:
    text = vacancy.haystack
    low = text.lower()

    blockers = [reason for word, reason in profile.BLOCKERS.items() if word in low]
    # Железо и сети: три и больше признаков - суть работы, блокер; меньше - пометка
    hands = sorted({label for pattern, label in profile.HANDS_ON.items() if re.search(pattern, low)})
    hands_note = ""
    if len(hands) >= 3:
        blockers.append("железо и сети как суть работы: " + ", ".join(hands))
    elif hands:
        hands_note = "есть железо/сети: " + ", ".join(hands)

    matched: list[tuple[str, str]] = []
    earned = 0
    for skill in profile.STRENGTHS:
        if skill.mentioned_in(text):
            matched.append((skill.name, skill.proof))
            earned += skill.weight

    penalty, gaps = 0, []
    for word, (weight, reason) in profile.GAPS.items():
        if word in low:
            penalty += weight
            gaps.append(reason)

    # Нормируем: 60 очков совпадений считаем полным попаданием
    raw = max(0, earned - penalty)
    value = min(100, round(raw / 60 * 100))

    notes = []
    if hands_note:
        notes.append(hands_note)

    track = detect_track(text, vacancy.name)
    head = (vacancy.name or "").lower()
    # Уровень и стаж в разработке: полтора года C# против senior/lead - это
    # не «стек совпал», это другая лига. В сопровождении «ведущий» - его уровень.
    # Иван 14.09: «почему сеньор питон наверху?» - потому что ключевые слова
    # совпадали, а уровень не считался. Теперь считается.
    if track in ("dev", "ai"):
        senior = ("senior", "сеньор", "lead", "лид", "head", "architect", "архитектор", "staff", "principal", "tech lead")
        if any(_hit(w, head) for w in senior):
            value = max(0, value - 30)
            notes.append("senior/lead в разработке - уровень выше опыта")
        exp = (vacancy.experience or "").lower()
        if "более 6" in exp or "6 лет" in exp:
            value = max(0, value - 15)
            notes.append("требуют от 6 лет в разработке")
    # Сопровождение с инфраструктурой - основное направление, ему приоритет в списке
    if track == "support":
        value = min(100, value + 10)
    top = vacancy.salary_to or vacancy.salary_from
    if top and top < profile.SALARY_FLOOR:
        value = max(0, value - 15)
        notes.append(f"вилка ниже планки: {vacancy.salary_text}")
    if not top:
        notes.append("вилка не указана - спрашивать первым вопросом")
    # График и смены не штрафуются и не отмечаются: решение Ивана 12.09 -
    # «ни график какой там - не интересует», задача - получить оффер.

    # Роль ниже уровня: семь лет опыта против «обучим с нуля» - это шаг назад,
    # даже когда по технологиям всё совпадает
    junior = ("готовы обучить", "без опыта", "нет опыта", "обучим", "стажёр", "junior")
    if any(word in low for word in junior):
        value = max(0, value - 20)
        notes.append("роль рассчитана на новичка - риск понижения уровня")

    # Работодатели с автоматическим скринингом на входе
    employer = (vacancy.employer or "").lower()
    for word, (weight, reason) in profile.EMPLOYERS.items():
        if word in employer:
            value = max(0, value - weight)
            notes.append(reason)
            break

    # Хелпдеск и работа с железом: стек может совпадать, но это шаг назад
    downgrades = [reason for word, reason in profile.DOWNGRADE.items() if word in low]
    if downgrades:
        value = max(0, value - 8 * len(downgrades))
        notes.extend(downgrades[:3])
    if "передача запросов на следующие" in low or "эскалировать на вторую" in low:
        value = max(0, value - 10)
        notes.append("по задачам это первая линия")

    return Verdict(score=value, track=track, matched=matched,
                   gaps=gaps, blockers=blockers, notes=notes)
