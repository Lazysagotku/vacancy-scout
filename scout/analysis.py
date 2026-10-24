# -*- coding: utf-8 -*-
"""Разбор находок: страница вакансии, оценка, письмо, мнение.

Вынесен из веб-слоя, потому что разбором занимается и фоновый скаут,
и человек по кнопке - логика должна быть одна.

Две ступени с разной ценой (ТЗ Ивана 12.09):
1. Открыть страницу и оценить по ключевым словам - секунды браузера,
   ноль токенов. Делается для всех, кто прошёл стоп-лист.
2. Письмо и мнение через Claude - 7 тысяч токенов. Делается для лучших
   по оценке в пределах бюджета на прогон, остальным - кнопка.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from scout import collector, profile, store, writer
from scout.hh import Vacancy
from scout.letter import draft
from scout.policy import salary_line, salary_track_for_resume
from scout.scoring import score

log = logging.getLogger(__name__)

# Письмо пишется всем, кроме явно мимо: Иван 12.09 - «сопрод не пишешь
# только тем, с которыми очень много несостыковок», «брать количеством».
LETTER_MIN_SCORE = 30
AUTO_LETTER_SCORE = 30

PAGE_FIELDS = ("employment", "hiring", "schedule", "hours", "work_format",
               "employer_rating", "employer_reviews", "key_skills")


def _vacancy(find: dict, description: str) -> Vacancy:
    return Vacancy(
        id=find["id"], name=find["name"], employer=find.get("employer") or "",
        url=find.get("url") or "", salary_from=find.get("salary_from"),
        salary_to=find.get("salary_to"), currency="RUR", schedule=None,
        experience=find.get("experience"), published=(find.get("found_at") or "")[:10],
        description=description,
    )


def _page_fields(find: dict) -> dict:
    return {key: find.get(key) for key in PAGE_FIELDS if find.get(key)}


def save_page(find: dict, details: dict) -> dict:
    """Кладёт в базу то, что снято со страницы: шапку, компанию, навыки, архив.

    Возвращает обновлённую находку. Архивную сразу отсеивает: письмо
    на закрытую вакансию - потерянные токены и ложная надежда.
    """
    fields = {key: details.get(key) for key in PAGE_FIELDS if details.get(key) not in (None, "")}
    if details.get("description"):
        fields["description"] = details["description"]
    if details.get("experience"):
        fields["experience"] = details["experience"]
    # Название компании со страницы полнее, чем из карточки выдачи,
    # где к нему прилипает рейтинг и обрезается хвост
    if details.get("employer"):
        fields["employer"] = details["employer"]
    # Название: у добавленной по ссылке находки его ещё нет, только заглушка
    if details.get("title") and (not find.get("name") or find["name"].startswith("вакансия ")):
        fields["name"] = details["title"]
    if details.get("archived_at"):
        fields["archived_at"] = details["archived_at"]
        fields["status"] = "dropped"
        fields["drop_reason"] = f"в архиве с {details['archived_at']}"
    if fields:
        store.update_find(find["id"], **fields)
    return store.get_find(find["id"]) or find


def _save(find: dict, description: str, write_letter: bool = True) -> dict:
    vacancy = _vacancy(find, description)
    # Навыки из шапки тоже идут в оценку: работодатель сам их выделил
    if find.get("key_skills"):
        vacancy.description = f"{description}\n\nКлючевые навыки: {find['key_skills']}"
    verdict = score(vacancy)
    vacancy.description = description
    # Если форму отклика уже разведывали, письмо собирается с оглядкой
    # на её вопросы: то, что спросят отдельным полем, в письме не нужно.
    from scout import response_form
    form = response_form.known(find["id"])
    resume = profile.resume_for(verdict.track, find.get("name", ""), description)

    fields = dict(
        status="analyzed",
        score=verdict.score,
        verdict=verdict.verdict,
        track=verdict.track,
        matched=", ".join(name for name, _ in verdict.matched),
        gaps="; ".join(verdict.gaps),
        blockers="; ".join(verdict.blockers),
        notes="; ".join(verdict.notes),
        resume=resume,
        description=description,
        analyzed_at=datetime.now().isoformat(timespec="seconds"),
    )
    # Шаблон - страховка: без VPN, без claude.exe, при лимите письмо всё равно есть
    if find.get("letter_kind") != "claude":
        fields["letter"] = draft(vacancy, verdict, form, resume)
        fields["letter_kind"] = "template"
    if write_letter and verdict.score >= LETTER_MIN_SCORE and description:
        written = writer.compose(vacancy, verdict, resume, form, extra=_page_fields(find))
        if written:
            fields["letter"], fields["review"] = written
            fields["letter_kind"] = "claude"
            fields["opinion"] = writer.opinion_of(fields["review"])
    store.update_find(find["id"], **fields)
    return store.get_find(find["id"]) or find


def write_letter(find: dict) -> dict | None:
    """Письмо и мнение через Claude для уже разобранной находки. None - не вышло."""
    description = find.get("description") or ""
    if not description:
        return None
    vacancy = _vacancy(find, description)
    verdict = score(vacancy)
    from scout import response_form
    resume = find.get("resume") or profile.resume_for(verdict.track, find["name"], description)
    written = writer.compose(vacancy, verdict, resume, response_form.known(find["id"]),
                             extra=_page_fields(find))
    if not written:
        return None
    letter, review = written
    store.update_find(find["id"], letter=letter, review=review, letter_kind="claude", resume=resume,
                      opinion=writer.opinion_of(review))
    return store.get_find(find["id"])


def catch_up_letters(budget: int, on_progress=None, now: datetime | None = None) -> int:
    """Письма для уже разобранных находок без письма Claude - лучшие первыми.

    Иван 14.09: «к рекомендую сразу пиши сопрод, чтобы я кнопку не жал и не ждал».
    Прогон пишет письма только своей пачке, а сильные из прошлых прогонов
    оставались с шаблоном. Здесь добираем их в пределах бюджета.
    """
    if budget <= 0:
        return 0
    now = now or datetime.now()

    def fresh(find: dict) -> bool:
        try:
            found = datetime.fromisoformat(find.get("found_at") or "")
            analyzed = datetime.fromisoformat(find.get("analyzed_at") or "")
            return (timedelta(0) <= now - found <= timedelta(hours=72)
                    and timedelta(0) <= now - analyzed <= timedelta(hours=72))
        except (TypeError, ValueError):
            return False

    def needs_letter(find: dict) -> bool:
        if find.get("letter_kind") != "claude":
            return True
        letter = find.get("letter") or ""
        if not writer._looks_like_letter(letter):
            return True
        # Зарплатная строка сверяется с приложенным резюме, а не с треком
        # политики: письмо писалось под резюме, и при расхождении двух
        # классификаторов (0,8% находок: «Technical support engineer» с
        # Python-резюме) письмо иначе переписывалось бы на каждом прогоне.
        resume = find.get("resume") or ""
        if not resume:
            return True
        return salary_line(salary_track_for_resume(resume)) not in letter

    todo = [f for f in store.list_finds("analyzed")
            if needs_letter(f) and (f.get("score") or 0) >= AUTO_LETTER_SCORE
            and (f.get("description") or "") and not f.get("archived_at") and fresh(f)]
    todo.sort(key=lambda f: -(f.get("score") or 0))
    done = 0
    for number, find in enumerate(todo[:budget], start=1):
        if on_progress:
            on_progress(number, min(len(todo), budget), stage="письма вдогонку")
        if write_letter(find) is None:
            break                                  # лимит или VPN - дальше те же провалы
        done += 1
    return done


def analyze_find(vacancy_id: str, deep: bool = True) -> dict:
    """Разбирает одну находку. Используется кнопкой в интерфейсе."""
    find = store.get_find(vacancy_id)
    if not find:
        raise LookupError(f"Находка {vacancy_id} не найдена")

    if deep and not (find.get("description") or ""):
        details = collector.fetch_details([vacancy_id]).get(vacancy_id) or {}
        find = save_page(find, details)
        if find.get("status") == "dropped":
            return find
    return _save(find, find.get("description") or "", write_letter=False)


def peek_forms(finds: list[dict], limit: int = 8, force: bool = False) -> int:
    """Снимает вопросы формы отклика у самых подходящих находок.

    Открывать форму у каждой вакансии дорого: это отдельная загрузка
    страницы плюс клик. Поэтому берём только тех, к кому реально пойдёт
    отклик - у остальных вопросы всё равно не понадобятся.

    `force` переснимает уже известные формы. Нужен после правок разбора: 23.10
    разбор научился отличать собственный виджет hh от вопроса работодателя и
    видеть кнопку сопроводительного, а в базе остались формы по прежним
    правилам.
    """
    from scout import response_form

    worth = [f for f in finds
             if (f.get("score") or 0) >= 60
             and (force or not response_form.known(f["id"]))]
    done = 0
    misses = 0
    for find in worth[:limit]:
        try:
            response_form.peek(find["id"])
            done += 1
            misses = 0
        except Exception as problem:
            # Отдельная вакансия может показать форму регистрации при живой
            # сессии: так ведут себя вакансии, требующие подтверждения входа.
            # Прежний голый break считал это потерей сессии и обрывал всю
            # пачку на первой же такой вакансии, оставляя остальных без форм.
            misses += 1
            log.warning("форма %s не снялась (%s подряд): %s: %s", find["id"],
                        misses, type(problem).__name__, str(problem)[:200])
            if misses >= 3:
                log.warning("три отказа подряд - похоже на потерю сессии, разведка прервана")
                break
    return done


def analyze_many(finds: list[dict], on_progress=None, letter_budget: int = 20) -> int:
    """Разбирает пачку в одном браузере. Возвращает число разобранных.

    Сначала страницы и оценка для всех - потом письма лучшим по оценке
    в пределах бюджета. Порядок важен: по заголовку не угадать, кто
    окажется сильнее после чтения описания.
    """
    todo = [f for f in finds if not (f.get("description") or "")]
    details = (
        collector.fetch_details([f["id"] for f in todo], on_progress=on_progress)
        if todo else {}
    )

    analyzed: list[dict] = []
    for find in finds:
        if find["id"] in details:
            find = save_page(find, details[find["id"]])
            if find.get("status") == "dropped":
                continue                              # архив
        description = find.get("description") or ""
        if not description:
            continue                                  # страница не отдалась - в следующий раз
        try:
            analyzed.append(_save(find, description, write_letter=False))
        except Exception as problem:
            # Одна сломанная карточка не должна останавливать пачку: раньше
            # исключение на любой вакансии поднималось до analyze_batch,
            # прогон возвращал ноль, и та же карточка валила каждый следующий
            # проход. В лог - только тип, без текста вакансии.
            log.error("разбор %s прерван: %s", find.get("id"), type(problem).__name__)

    # Письма: лучшие первыми, в пределах бюджета. Уже написанные не трогаем.
    worth = sorted((f for f in analyzed
                    if (f.get("score") or 0) >= AUTO_LETTER_SCORE and f.get("letter_kind") != "claude"),
                   key=lambda f: -(f.get("score") or 0))
    written = 0
    for number, find in enumerate(worth[:max(0, letter_budget)], start=1):
        if on_progress:
            on_progress(number, len(worth[:letter_budget]), stage="письма")
        if write_letter(find) is None:
            # Один провал - почти всегда выключенный VPN или лимит: дальше
            # будут те же провалы. Остальным - кнопка.
            return len(analyzed)
        written += 1
    # Остаток бюджета - на сильные из прошлых прогонов
    catch_up_letters(max(0, letter_budget - written), on_progress)
    return len(analyzed)
