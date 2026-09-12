# -*- coding: utf-8 -*-
"""Письмо под конкретную вакансию - через Claude в фоновом режиме.

Шаблонные абзацы из letter.py остаются запасным вариантом: они нужны,
когда Claude недоступен (нет VPN, нет claude.exe, кончился лимит). Основной
путь - здесь: вакансия целиком, факты об Иване целиком, и письмо пишется
под то, что работодатель написал сам. Решение Ивана 11.09: «как работодатель
даёт уникальный стек - так и мы даём уникальное письмо».

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

FACTS = Path(__file__).with_name("facts.md")
TIMEOUT = 180                      # секунд на одно письмо; обычно уходит 30-40
MODEL = "opus"
FALLBACK_MODEL = "sonnet"          # если opus перегружен - лучше письмо от sonnet, чем шаблон


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


SYSTEM = ("Ты пишешь сопроводительные письма на русском языке от лица соискателя Ивана Архипова. "
          "Отвечаешь только текстом письма: без пояснений, без кавычек, без разметки.")

PROMPT = """Напиши сопроводительное письмо от лица Ивана Архипова на вакансию ниже.

ПРАВИЛА ПИСЬМА, БЕЗ ИСКЛЮЧЕНИЙ:
1. Начало ровно такое: «Добрый день!», пустая строка, «Откликаюсь на позицию {name}.», пустая строка.
2. Первый содержательный абзац цепляется за ГЛАВНОЕ требование или формулировку из текста вакансии - за то, что работодатель написал сам. Если он что-то подчеркнул, повторил, выделил - письмо начинается с этого.
3. Дальше три-четыре абзаца-истории под конкретные требования вакансии. Каждый абзац - действие и результат из фактов, с цифрой, где она есть. Не перечень навыков.
4. Чего нет в опыте - одной строкой ближе к концу, без извинений, сразу с тем, что делается вместо. Только то, что вакансия действительно требует.
5. Конец ровно такой: пустая строка, «Мои проекты: https://lazysagotku.github.io», пустая строка, «Готов обсудить детали.», пустая строка, «С уважением,», «Иван Архипов».
6. Объём: 180-280 слов между приветствием и подписью.
7. Только дефис «-». Длинное тире запрещено.
8. Конструкции «не X, а Y», «это не просто X, это Y», «не понаслышке» запрещены. Только X.
9. Никакого markdown: ни звёздочек, ни списков, ни заголовков. Сплошной текст абзацами.
10. Никакого самоуничижения: «буду осваивать с нуля», «понимаю, что не заменяет», «к сожалению», «освоюсь быстро» - запрещены.
11. Не приписывать Ивану разработку трекера и разведчика - они собраны через Claude Code.
12. Факты берутся ТОЛЬКО из раздела ФАКТЫ ниже. Действие, которого там нет, в письмо не попадает. Соседнее действие не додумывать: из «сопоставлял данные при миграции» не следует «проектировал схемы», из «участвовал во внедрении» не следует «руководил внедрением».
13. Про стенд - только раздел «Сделано». Всё из раздела «Впереди» упоминается словами «сейчас поднимаю», никогда «поднял».
14. Ответ - только текст письма. Без пояснений до и после, без кавычек вокруг.

КАКОЕ РЕЗЮМЕ ПРИЛОЖЕНО: {resume}. Письмо должно ему соответствовать: сопровожденец говорит про инциденты, эксплуатацию и автоматизацию; разработчик - про код, архитектуру, базы; Python-разработчик - про Python в проде и Django.

{form_block}
=== ФАКТЫ ОБ ИВАНЕ ===
{facts}

=== ВАКАНСИЯ ===
Название: {name}
Работодатель: {employer}
{description}
"""


def compose(vacancy: Vacancy, verdict: Verdict, resume: str, form: dict | None = None) -> str | None:
    """Пишет письмо. None - если Claude недоступен или ответил мусором."""
    exe = binary()
    if not exe or not reachable():
        log.info("writer: claude недоступен (exe=%s)", bool(exe))
        return None

    form_block = ""
    questions = (form or {}).get("questions") or []
    if questions:
        form_block = ("В ФОРМЕ ОТКЛИКА РАБОТОДАТЕЛЬ ЗАДАЁТ ОТДЕЛЬНЫЕ ВОПРОСЫ - их темы в письме не раскрывать подробно, "
                      "на них будет отдельный ответ:\n- " + "\n- ".join(str(q) for q in questions[:6]) + "\n")

    prompt = PROMPT.format(
        name=vacancy.name.strip(),
        employer=(vacancy.employer or "не указан").strip(),
        description=(vacancy.description or "").strip()[:9000],
        resume=resume,
        facts=FACTS.read_text(encoding="utf-8"),
        form_block=form_block,
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
    text = _clean(run.stdout)
    if not _looks_like_letter(text):
        log.warning("writer: %s - ответ не похож на письмо (%d символов): %r", vacancy.id, len(text), text[:120])
        return None
    return text


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
