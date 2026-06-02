"""
llm_enricher.py — обогащает расписание через LLM:
  - контекст из корп. базы знаний по митингам
  - релевантные Jira-тикеты для Fokus Arbeit задач

Использует Abacus.AI RouteLLM API (OpenAI-совместимый).
"""
import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── LLM client ─────────────────────────────────────────────────────────────────

def _call_llm(system: str, user: str) -> str:
    """
    Вызов LLM через Abacus.AI RouteLLM API (OpenAI-совместимый).
    """
    client = OpenAI(
        base_url=os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1"),
        api_key=os.getenv("ABACUS_API_KEY"),
    )
    response = client.chat.completions.create(
        model=os.getenv("ABACUS_MODEL", "gpt-5"),
        max_tokens=2000,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content


# ── Enrich ─────────────────────────────────────────────────────────────────────

def enrich_schedule(schedule_content: str) -> str:
    """
    Принимает текущий schedule.md, возвращает обогащённую версию.
    
    Что делает LLM:
    1. Для каждого митинга — добавляет комментарий с контекстом/вопросами
    2. Для Fokus Arbeit задач — предлагает связанные Jira-тикеты из списка
    """
    # Загружаем тикеты для контекста
    jira_context = ""
    try:
        from jira_sync import fetch_my_tickets, tickets_as_todo_lines
        tickets = fetch_my_tickets()
        if tickets:
            ticket_lines = "\n".join(f"- {t['key']}: {t['summary']} [{t['status']}]" for t in tickets)
            jira_context = f"\n\nАктивные Jira-тикеты:\n{ticket_lines}"
    except Exception as e:
        jira_context = f"\n\n(Jira недоступна: {e})"

    system_prompt = """Ты помощник по планированию рабочего дня.
Тебе дают schedule.md файл с расписанием.
Твоя задача — обогатить его полезным контекстом.

Соглашения о формате:
- Митинги из календаря начинаются с "Meeting:" в секции ## Schedule
- Всё остальное (не Meeting, не Pause) — задачи фокусной работы

Правила:
1. Для каждого митинга в секции ## Meetings добавь строку комментария:
   > 💡 <короткий контекст: цель митинга, что подготовить, ключевые вопросы>

2. Для задач фокусной работы в ## Schedule (всё что НЕ начинается с "Meeting:" и не пауза) добавь связанные тикеты из списка если они релевантны:
   > 🎫 Связанные тикеты: PROJ-123, PROJ-456

3. НЕ меняй структуру файла, не удаляй существующие строки.
4. Добавляй только строки начинающиеся с "> " после соответствующих элементов.
5. Отвечай только обогащённым файлом, без объяснений."""

    user_prompt = f"""Обогати это расписание:{jira_context}

--- РАСПИСАНИЕ ---
{schedule_content}
--- КОНЕЦ ---"""

    return _call_llm(system_prompt, user_prompt)


if __name__ == "__main__":
    # Тест
    sample = """# 2026-06-02

## Meetings
- 09:00 Standup mit Team
- 14:00 1:1 mit Manager

## Schedule
- Standup mit Team 09:00-09:30
- Fokus Arbeit: Feature X 09:30-12:00
- Mittagspause 12:00-13:00

## Todo
- [ ] PR reviewen
"""
    print(enrich_schedule(sample))
