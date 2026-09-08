# -*- coding: utf-8 -*-
"""Бриф по вакансии:материал, из которого пишется письмо.

Зачем понадобился. Раньше разведчик отдавал наверх только список совпавших
навыков, и письмо собиралось из готовых кусков под эти слова. Терялось всё
остальное: чем компания занимается, как она сама себя описывает, какие фразы
в объявлении важны для неё. Письмо выходило шаблонным, потому что шаблонным
был вход.

Теперь модуль разбирает описание целиком и готовит материал: разделы,
зацепки, требования по пунктам. Письмо по этому материалу пишет человек
или ассистент, а не подстановка в шаблон.
"""

from __future__ import annotations

import re

# Заголовки разделов, которыми работодатели размечают объявления
SECTIONS = [
    ("duties", ("чем предстоит заниматься", "обязанности", "задачи", "что нужно будет делать",
                "что предстоит", "ваши задачи", "функционал")),
    ("requirements", ("что мы ожидаем", "требования", "от вас нужны", "что должно быть в руках",
                      "мы ждём", "мы ждем", "наши требования", "необходимые навыки", "ожидаем")),
    ("nice", ("будет плюсом", "плюсом будет", "желательно", "приветствуется", "дополнительно")),
    ("offer", ("что мы предлагаем", "условия", "мы предлагаем", "почему стоит", "мы даём")),
    ("about", ("о компании", "о нас", "о проекте", "о продукте", "кто мы")),
]

# Фразы, которыми компания говорит о себе что-то небанальное. За такое
# цепляется первый абзац письма: видно, что объявление прочитано.
HOOKS = (
    "не программист", "не ищем", "ищем не", "принципиально", "важно для нас",
    "у нас нет", "мы не", "единственный инженер", "без бюрократии", "команда одного продукта",
    "компания одного продукта", "готовы обучать", "научим", "с нуля", "нетоксичн",
    "демократичн", "полная удалёнка", "полная удаленка", "гибкий график",
)


def _split_sections(text: str) -> dict[str, str]:
    """Режет описание по заголовкам разделов."""
    low = text.lower()
    marks: list[tuple[int, str]] = []
    for key, titles in SECTIONS:
        # Берём все вхождения, а не первое: работодатели пишут требования
        # дважды - коротко в начале («от вас нужны») и списком в конце.
        for title in titles:
            start = 0
            while True:
                pos = low.find(title, start)
                if pos < 0:
                    break
                marks.append((pos, key))
                start = pos + len(title)
    marks.sort()

    out: dict[str, str] = {}
    for i, (pos, key) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chunk = text[pos:end].strip()
        if len(chunk) > 40:
            # Разделов с одним ключом может быть несколько - склеиваем
            out[key] = (out.get(key, "") + "\n" + chunk).strip()
    if not out:
        out["all"] = text
    return out


def _bullets(chunk: str, limit: int = 14) -> list[str]:
    """Достаёт пункты списка из куска текста.

    В выгрузке hh переносы часто съедены, поэтому режем и по переводам
    строк, и по точкам с запятой, и по маркерам списка.
    """
    raw = re.split(r"[\n;•·]|(?<=[а-яё])\.\s+(?=[А-ЯA-Z])", chunk)
    items = []
    for piece in raw:
        piece = " ".join(piece.split()).strip(" -–—.")
        if 12 < len(piece) < 220:
            items.append(piece)
    return items[1:limit + 1]      # первый кусок - сам заголовок раздела


def _hooks(text: str) -> list[str]:
    """Находит фразы, за которые стоит зацепиться в первом абзаце.

    Куски вокруг разных слов часто перекрываются - «не программист» и
    «готовы обучать» в одном предложении дают почти одинаковые цитаты.
    Поэтому близкие позиции схлопываем в одну.
    """
    low = text.lower()
    spots: list[int] = []
    for word in HOOKS:
        pos = low.find(word)
        if pos >= 0 and all(abs(pos - seen) > 160 for seen in spots):
            spots.append(pos)

    found = []
    for pos in sorted(spots)[:4]:
        start = max(0, pos - 90)
        end = min(len(text), pos + 150)
        found.append("…" + " ".join(text[start:end].split()) + "…")
    return found


def build(find: dict) -> dict:
    """Готовит материал для письма по одной находке."""
    text = find.get("description") or ""
    if not text:
        return {"empty": True}

    sections = _split_sections(text)
    return {
        "empty": False,
        "name": find.get("name"),
        "employer": find.get("employer"),
        "url": find.get("url"),
        "score": find.get("score"),
        "matched": find.get("matched"),
        "gaps": find.get("gaps"),
        "resume": find.get("resume"),
        "duties": _bullets(sections.get("duties", "")),
        "requirements": _bullets(sections.get("requirements", "")),
        "nice": _bullets(sections.get("nice", "")),
        "offer": _bullets(sections.get("offer", ""), limit=8),
        "about": " ".join(sections.get("about", "")[:600].split()),
        "hooks": _hooks(text),
        "full": " ".join(text.split()),
    }


def as_text(find: dict) -> str:
    """Бриф одной строкой текста - в таком виде его удобно читать и передавать."""
    data = build(find)
    if data.get("empty"):
        return "Описание не загружено - сначала разбор вакансии."

    lines = [f"{data['name']} · {data['employer']}", f"{data['url']}",
             f"оценка {data['score']} · резюме: {data['resume']}", ""]

    if data["hooks"]:
        lines += ["ЗА ЧТО ЗАЦЕПИТЬСЯ (как компания говорит о себе):"]
        lines += [f"  {h}" for h in data["hooks"]] + [""]
    for title, key in (("ТРЕБОВАНИЯ", "requirements"), ("ЗАДАЧИ", "duties"),
                       ("БУДЕТ ПЛЮСОМ", "nice"), ("УСЛОВИЯ", "offer")):
        if data[key]:
            lines += [f"{title}:"] + [f"  - {b}" for b in data[key]] + [""]
    if data["about"]:
        lines += ["О КОМПАНИИ:", f"  {data['about']}", ""]

    lines += [f"СОВПАЛО: {data['matched'] or '-'}", f"ПРОБЕЛЫ: {data['gaps'] or 'нет'}"]
    return "\n".join(lines)
