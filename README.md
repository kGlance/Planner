# 📅 Planner — локальный сервер расписания

## Быстрый старт

### 1. Установка зависимостей
```bash
cd C:\Users\ekoch\Code\planner
pip install -r requirements.txt

# Для Outlook sync (обязательно на Windows):
pip install pywin32
```

### 2. Настройка .env
```bash
copy .env.example .env
# Открой .env и заполни свои данные
```

Минимально нужно заполнить:
- `ANTHROPIC_API_KEY` — ключ с claude.ai → Settings → API Keys
- `JIRA_BASE_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`
- `SCHEDULE_FILE` — путь к твоему schedule.md

### 3. Запуск
```bash
python server.py
```
Откроется `http://localhost:810X` — порт подберётся автоматически.

---

## Файлы проекта

| Файл | Что делает |
|---|---|
| `server.py` | FastAPI сервер, авто-порт, отдаёт UI |
| `outlook_sync.py` | Читает митинги из Outlook, мержит в schedule.md |
| `jira_sync.py` | Загружает твои активные тикеты из Jira |
| `llm_enricher.py` | LLM добавляет контекст к митингам и задачам |
| `index.html` | Браузерный UI (timeline + редактор) |
| `schedule.md` | Твой файл расписания |
| `.env` | Настройки (не коммить в git!) |

---

## Outlook sync — что нужно знать

`win32com` — это Python-библиотека которая управляет Outlook как будто ты кликаешь по нему сама.  
Outlook должен быть **открыт** при запуске sync.  
Устанавливается через `pip install pywin32`.

---

## Переход на Azure OpenAI

В `llm_enricher.py` замени функцию `_call_llm`:

```python
import openai

def _call_llm(system: str, user: str) -> str:
    client = openai.AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version="2024-02-01",
    )
    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return response.choices[0].message.content
```

---

## .gitignore
```
.env
schedule.md
__pycache__/
*.pyc
```
