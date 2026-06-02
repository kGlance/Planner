# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Запуск

```bash
pip install -r requirements.txt
# Windows, для Outlook sync:
pip install pywin32

python server.py
# Открывается http://localhost:8100 (порт подбирается автоматически начиная с 8100)
```

Сервер запускается с `reload=True` — изменения в `.py` файлах подхватываются автоматически. `index.html` отдаётся через FastAPI, не через отдельный dev-сервер.

## .env — обязательные переменные

```
ABACUS_API_KEY=...          # ключ Abacus.AI RouteLLM
ABACUS_BASE_URL=https://routellm.abacus.ai/v1
ABACUS_MODEL=gpt-5

JIRA_BASE_URL=https://jira.company.com
JIRA_USERNAME=e.koch
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=PROJ

OUTLOOK_CALENDAR_NAME=Kalender   # имя календаря в Outlook
OUTLOOK_DAYS_AHEAD=7

SCHEDULE_FILE=C:\Users\...\schedule.md
SERVER_PORT_START=8100
```

## Архитектура

Локальный FastAPI-сервер (`server.py`) + single-page UI (`index.html`). Никакой БД — единственное хранилище данных это `schedule.md` на диске.

### Поток данных при старте страницы

1. `startup()` в `index.html` — читает `schedule.md` без парсинга (`loadSchedule(false)`)
2. Вызывает `POST /api/sync/outlook` — Outlook → файл обновляется на диске, возвращается новое содержимое
3. Один вызов `triggerParse()` — `POST /api/parse` → LLM (with rescheduling and conflict resolution) → JSON → рисует timeline. LLM при этом: раскладывает duration-only задачи по слотам, разрешает конфликты (сдвигает fokus-задачи, если они перекрываются с митингами), помечает неразрешимые конфликты (meeting vs meeting) флагом `conflict: true`

### Формат schedule.md

Дни разделяются строкой `---`. Каждый день:

```markdown
# YYYY-MM-DD

## Meetings
- HH:MM Название митинга

## Schedule
- Meeting: Название HH:MM-HH:MM   ← митинги из Outlook (префикс "Meeting:" обязателен)
- Задача HH:MM-HH:MM               ← фиксированное время
- Задача 2 Stunden                 ← только длительность, LLM сама ставит в слот

## Todo
- [ ] задача
```

### API endpoints

| Метод | URL | Что делает |
|---|---|---|
| GET | `/api/schedule` | Читает schedule.md |
| POST | `/api/schedule` | Сохраняет schedule.md (raw текст) |
| POST | `/api/parse` | LLM парсит md → JSON для timeline |
| POST | `/api/sync/outlook` | Читает Outlook, мержит в файл, возвращает обновлённый контент |
| GET | `/api/sync/jira` | Возвращает активные тикеты из Jira |
| POST | `/api/enrich` | LLM добавляет контекст к митингам и связывает с Jira-тикетами |
| POST | `/api/task` | LLM вставляет новую задачу из natural-language команды |

### Модули

**`outlook_sync.py`** — `get_outlook_meetings()` читает через `win32com` только события в окне `today..today+DAYS_AHEAD`. Outlook `Restrict()` ненадёжен для recurring-событий, поэтому применяется жёсткая Python-фильтрация по дате при итерации. `fetch_and_merge()` мержит митинги в существующие дни и сохраняет только дни внутри окна (старые дни отбрасываются).

**`jira_sync.py`** — сначала GET `{JIRA_BASE_URL}/rest/api/2/myself` для получения `accountId`, затем JQL-запрос. Поддерживает Jira Cloud (`accountId`) и Jira Server/DC (`name`).

**`llm_enricher.py`** — `enrich_schedule()` подтягивает Jira-тикеты и передаёт их LLM вместе с расписанием. LLM добавляет строки `> 💡` после митингов и `> 🎫` после fokus-задач. Файл не изменяет — возвращает строку клиенту.

**`server.py`** — `clean_schedule()` вызывается после любой LLM-записи в файл: схлопывает множественные `---`, убирает лишние пустые строки.

### LLM — ключевые соглашения в промптах

**`/api/parse`** — LLM выступает планировщиком: раскладывает duration-only задачи в свободные слоты, разбивает длинные задачи на части (сумма частей = оригинальная длительность), переносит остаток на следующий рабочий день. Встречи (Meeting:) и паузы — неподвижны; fokus-задачи сдвигаются при конфликте. Выходные пропускаются. Возвращает JSON, не md.

**`/api/task`** — LLM только вставляет одну строку в существующий файл. Не переписывает, не переформатирует. Если день занят — переходит к следующему рабочему дню (до 14 дней вперёд).

**`/api/enrich`** — LLM только добавляет строки `> ...` после существующих, ничего не удаляет.

### Категории в timeline

- `meeting` — начинается с `"Meeting:"` или совпадает с записью в `## Meetings`
- `pause` — содержит: pause, mittagspause, kaffeepause, frei luft, freiluft, break, lunch
- `fokus` — всё остальное (пользователь не пишет "Fokus Arbeit" — LLM категоризирует сама)

Конфликт meeting vs meeting (оба из Outlook) — рисуется красная полоска слева + `⚠` в тултипе, не разрешается автоматически.

### TO-DO
- git
- скролл в time-line вниз
- проверить task, можно ли добавлять несолько одновременно, можно ли добавлять не на сегодня, а на потом, можно ли добавлять без даты, а только длительность
- jira не проверена