# -*- coding: utf-8 -*-
"""Письмо и мнение под конкретную вакансию - через Claude в фоновом режиме.

Шаблонные абзацы из letter.py остаются запасным вариантом: они нужны,
когда Claude недоступен (нет VPN, нет claude.exe, кончился лимит). Основной
путь - здесь: вакансия целиком, факты об Иване, и письмо пишется под то,
что работодатель написал сам. Решение Ивана 11.09: «как работодатель
даёт уникальный стек - так и мы даём уникальное письмо».

Тем же вызовом приходит мнение: рекомендую или нет и почему. Иван 12.09:
«пока меня не позвали хотя бы на первичный созвон - делаем только сопрод
письмо и мнение». Отдельный проход по вакансиям в чате не нужен.

Вызов идёт через claude.exe из расширения VSCode - тот же, что у сторожа
контекста. Ключ API не нужен, работает подписка Claude Code.

Почему такие флаги запуска (выяснено 12.09, первый вариант висел 180 с):
- без --strict-mcp-config и --setting-sources "" claude поднимает все
  MCP-серверы проекта (playwright, firecrawl, память кода) и хуки - это
  минуты на старте ради письма, которому они не нужны;
- --tools "" запрещает инструменты вовсе: модели нечего читать и запускать,
  всё уже в промпте;
- промпт идёт через stdin: 18 КБ кириллицы аргументом командной строки
  Windows живут на грани лимита;
- opus пишет за 30-40 с и держится фактов, sonnet - 60-80 с и додумывает.

Экономия токенов: факты разрезаны по трекам (common + support или dev),
сопровожденцу не нужны Contact Manager и EF Core, разработчику - Zabbix.
Правила и факты стоят в промпте первыми, вакансия последней - неизменный
префикс кэшируется на стороне API, когда письма идут подряд.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import socket
import subprocess
from pathlib import Path

from scout.hh import Vacancy
from scout.scoring import Verdict

log = logging.getLogger(__name__)

FACTS_DIR = Path(__file__).with_name("facts")
TIMEOUT = 180                      # секунд на одно письмо; обычно уходит 30-40
MODEL = "opus"
FALLBACK_MODEL = "sonnet"          # если opus перегружен - лучше письмо от sonnet, чем шаблон

# Какие файлы фактов идут под какой трек. «ai» - это разработка с ИИ-инструментами.
TRACK_FACTS = {
    "support": ("common", "support"),
    "dev": ("common", "dev"),
    "ai": ("common", "dev"),
}


def binary() -> str | None:
    """claude.exe из самого свежего расширения VSCode."""
    pattern = os.path.expanduser(r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe")
    found = sorted(glob.glob(pattern), reverse=True)
    return found[0] if found else None


def reachable(timeout: float = 3.0) -> bool:
    """Доходит ли до API. Без VPN не доходит - тогда письмо шаблонное."""
    try:
        with socket.create_connection(("api.anthropic.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


def available() -> bool:
    return binary() is not None and reachable()


def facts_for(track: str | None) -> str:
    names = TRACK_FACTS.get(track or "", TRACK_FACTS["support"])
    return "\n\n".join((FACTS_DIR / f"{name}.md").read_text(encoding="utf-8") for name in names)


SYSTEM = ("Ты пишешь сопроводительные письма на русском языке от лица соискателя Ивана Архипова "
          "и коротко оцениваешь вакансию. Отвечаешь строго в заданном формате, без пояснений, "
          "без кавычек, без разметки.")

MARK = "=== МНЕНИЕ ==="

PROMPT = """Напиши сопроводительное письмо от лица Ивана Архипова на вакансию ниже, затем мнение о вакансии.

ПРАВИЛА ПИСЬМА, БЕЗ ИСКЛЮЧЕНИЙ:
1. Начало ровно такое: «Добрый день!», пустая строка, «Откликаюсь на позицию {name}.», пустая строка.
2. Первый содержательный абзац цепляется за ГЛАВНОЕ требование или формулировку из текста вакансии - за то, что работодатель написал сам. Если он что-то подчеркнул, повторил, выделил - письмо начинается с этого.
3. Дальше три-четыре абзаца-истории под конкретные требования вакансии. Каждый абзац - действие и результат из фактов, с цифрой, где она есть. Не перечень навыков.
4. Названия работодателей и мест работы не упоминаются никак: ни «в банке», ни «в госструктуре», ни «на прошлом месте». Только действие: «настраивал», «перенёс», «сверял».
5. Чего нет в опыте - одной строкой ближе к концу, без извинений, сразу с тем, что делается вместо. Только то, что вакансия действительно требует.
6. Если работодатель в тексте просит что-то указать в письме (зарплатные ожидания, способ связи, ответ на вопрос) - это должно быть в письме отдельной строкой перед подписью. Формулировка зарплатных ожиданий - в фактах.
7. Конец ровно такой: пустая строка, «Мои проекты: https://lazysagotku.github.io», пустая строка, «Готов обсудить детали.», пустая строка, «С уважением,», «Иван Архипов».
8. Объём: 180-280 слов между приветствием и подписью.
9. Только дефис «-». Длинное тире запрещено.
10. Конструкции «не X, а Y», «это не просто X, это Y», «не понаслышке» запрещены. Только X.
11. Никакого markdown: ни звёздочек, ни списков, ни заголовков. Сплошной текст абзацами.
12. Никакого самоуничижения: «буду осваивать с нуля», «понимаю, что не заменяет», «к сожалению», «освоюсь быстро» - запрещены.
13. Не приписывать Ивану разработку трекера и разведчика - они собраны через Claude Code.
14. Факты берутся ТОЛЬКО из раздела ФАКТЫ ниже. Действие, которого там нет, в письмо не попадает. Соседнее действие не додумывать: из «сопоставлял данные при миграции» не следует «проектировал схемы», из «участвовал во внедрении» не следует «руководил внедрением».
15. Про стенд - только раздел «Сделано». Всё из раздела «Впереди» упоминается словами «сейчас поднимаю», никогда «поднял».

КАКОЕ РЕЗЮМЕ ПРИЛОЖЕНО: {resume}. Письмо должно ему соответствовать: сопровожденец говорит про инциденты, эксплуатацию и автоматизацию; разработчик - про код, архитектуру, базы; Python-разработчик - про Python в проде и Django.

{form_block}
ФОРМАТ ОТВЕТА. Сначала текст письма. Потом отдельной строкой ровно «{mark}». Потом мнение: первая строка - одно из «Рекомендую», «Не рекомендую», «Под вопросом»; дальше две-четыре строки причин, каждая с новой строки, коротко и по делу: совпадение с опытом, условия (оформление, формат, график), рейтинг работодателя, что настораживает в тексте. Иван откликается почти на всё - «Не рекомендую» только когда совпадений с опытом нет вовсе или условия неприемлемы.

=== ФАКТЫ ОБ ИВАНЕ ===
{facts}

=== ВАКАНСИЯ ===
Название: {name}
Работодатель: {employer}{employer_line}
Условия: {conditions}
Ключевые навыки по версии работодателя: {skills}

{description}
"""


def _conditions(vacancy: Vacancy, extra: dict) -> str:
    parts = []
    for key in ("employment", "hiring", "schedule", "hours", "work_format"):
        if extra.get(key):
            parts.append(str(extra[key]))
    if extra.get("experience") or vacancy.experience:
        parts.append("опыт " + str(extra.get("experience") or vacancy.experience))
    if vacancy.salary_from or vacancy.salary_to:
        parts.append(vacancy.salary_text)
    return "; ".join(parts) or "не указаны"


def compose(vacancy: Vacancy, verdict: Verdict, resume: str, form: dict | None = None,
            extra: dict | None = None) -> tuple[str, str] | None:
    """Пишет письмо и мнение. None - если Claude недоступен или ответил мусором.

    extra - поля со страницы вакансии (оформление, график, рейтинг, навыки),
    когда они есть: по ним мнение отличает «удалённо по ТД» от «ГПХ в офисе».
    """
    exe = binary()
    if not exe or not reachable():
        log.info("writer: claude недоступен (exe=%s)", bool(exe))
        return None

    extra = extra or {}
    form_block = ""
    questions = (form or {}).get("questions") or []
    if questions:
        form_block = ("В ФОРМЕ ОТКЛИКА РАБОТОДАТЕЛЬ ЗАДАЁТ ОТДЕЛЬНЫЕ ВОПРОСЫ - их темы в письме не раскрывать подробно, "
                      "на них будет отдельный ответ:\n- " + "\n- ".join(str(q) for q in questions[:6]) + "\n")

    employer_line = ""
    if extra.get("employer_rating"):
        employer_line = f" (рейтинг на hh {extra['employer_rating']}, отзывов {extra.get('employer_reviews') or '?'})"

    prompt = PROMPT.format(
        name=vacancy.name.strip(),
        employer=(vacancy.employer or "не указан").strip(),
        employer_line=employer_line,
        conditions=_conditions(vacancy, extra),
        skills=extra.get("key_skills") or "не указаны",
        description=(vacancy.description or "").strip()[:9000],
        resume=resume,
        facts=facts_for(verdict.track),
        form_block=form_block,
        mark=MARK,
    )
    cmd = [
        exe, "-p", "--output-format", "text",
        "--model", MODEL, "--fallback-model", FALLBACK_MODEL,
        "--system-prompt", SYSTEM,
        "--tools", "", "--strict-mcp-config", "--setting-sources", "",
    ]
    try:
        run = subprocess.run(
            cmd, input=prompt,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.TimeoutExpired:
        log.warning("writer: %s - таймаут %s с", vacancy.id, TIMEOUT)
        return None
    except OSError as e:
        log.warning("writer: %s - не запустился: %s", vacancy.id, e)
        return None
    if run.returncode != 0:
        log.warning("writer: %s - rc=%s, stderr: %s", vacancy.id, run.returncode, run.stderr.strip()[-500:])
        return None

    letter, review = _split(run.stdout)
    letter = _clean(letter)
    if not _looks_like_letter(letter):
        log.warning("writer: %s - ответ не похож на письмо (%d символов): %r", vacancy.id, len(letter), letter[:120])
        return None
    return letter, _clean(review)


def _split(text: str) -> tuple[str, str]:
    """Письмо и мнение. Метку модель иногда пишет без знаков равенства."""
    m = re.search(r"\n\s*=*\s*МНЕНИЕ\s*=*\s*\n", text)
    if not m:
        return text, ""
    return text[:m.start()], text[m.end():]


def _clean(text: str) -> str:
    text = text.strip()
    # Модель иногда оборачивает ответ в кавычки или тройные бэктики - снять
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip("«»\"' \n")
    # Длинное тире снимаем принудительно - правило Ивана
    text = text.replace("—", "-").replace("–", "-")
    return text.strip()


def _looks_like_letter(text: str) -> bool:
    """Отсев мусора: пояснение вместо письма, пустота, обрезок."""
    if len(text) < 400:
        return False
    if not text.startswith("Добрый день"):
        return False
    if "Иван Архипов" not in text[-60:]:
        return False
    if "lazysagotku.github.io" not in text:
        return False
    return True
