"""
server.py — главный FastAPI сервер планировщика
Запуск: python server.py
"""
import os
import sys
import socket
import uvicorn

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Planner API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SCHEDULE_FILE = Path(os.getenv("SCHEDULE_FILE", "schedule.md"))
CACHE_FILE = SCHEDULE_FILE.parent / "timeline_cache.json"


def find_free_port(start: int = 8100) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    raise RuntimeError("Не нашёл свободный порт в диапазоне 8100-8120")


# ── Утилиты ────────────────────────────────────────────────────────────────────

import re as _re

def clean_schedule(text: str) -> str:
    """Убирает мусор который LLM добавляет в файл расписания."""
    # Схлопываем несколько --- подряд (с пустыми строками между ними) в один
    text = _re.sub(r'(\n\s*---\s*){2,}', '\n\n---\n', text)
    # Убираем --- в самом начале или конце файла
    text = _re.sub(r'^\s*---\s*\n', '', text)
    text = _re.sub(r'\n\s*---\s*$', '', text)
    # Убираем тройные и более пустые строки
    text = _re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


# ── Расписание ─────────────────────────────────────────────────────────────────

@app.get("/api/schedule")
def read_schedule():
    if not SCHEDULE_FILE.exists():
        return {"content": "", "path": str(SCHEDULE_FILE)}
    return {"content": SCHEDULE_FILE.read_text(encoding="utf-8"), "path": str(SCHEDULE_FILE)}


class ScheduleBody(BaseModel):
    content: str

@app.post("/api/schedule")
def write_schedule(body: ScheduleBody):
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": str(SCHEDULE_FILE)}


@app.get("/api/cache")
def read_cache():
    if not CACHE_FILE.exists():
        return {"ok": False, "data": None}
    import json
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return {"ok": True, "data": data}
    except Exception:
        return {"ok": False, "data": None}


class CacheBody(BaseModel):
    data: dict

@app.post("/api/cache")
def write_cache(body: CacheBody):
    import json
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(body.data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


# ── Шаг 1: планирование — LLM переписывает schedule.md ────────────────────────

class ParseBody(BaseModel):
    content: str

@app.post("/api/plan")
def plan_schedule(body: ParseBody):
    from openai import OpenAI
    import time

    api_key = os.getenv("ABACUS_API_KEY")
    if not api_key:
        raise HTTPException(500, "ABACUS_API_KEY не задан в .env")

    system_prompt = """You are a work-day schedule planner. Your job is to take a schedule.md file and return the COMPLETE updated schedule.md with all duration-only tasks placed into concrete time slots.

## Output format
Return ONLY the raw updated schedule.md content — no markdown fences, no explanation, no JSON.
Preserve all existing structure exactly: day separators (---), section headers (## Meetings, ## Schedule, ## Todo), meeting lines, todo items, done markers.

## Duration syntax — convert to fixed times
Items in ## Schedule may specify duration instead of a time range. Recognize these patterns (case-insensitive, German/English):
- "2 Stunde" / "2 Stunden" / "2h" → 120 min
- "30 Minuten" / "30 Min" / "30m" → 30 min
- "1,5 Stunde" / "1.5h" → 90 min
Convert each duration-only item to "Item HH:MM-HH:MM" format. Items that already have HH:MM-HH:MM must not be changed.

## Scheduling rules (applied independently for EVERY day)
1. First, lay out all fixed-time items (HH:MM-HH:MM or explicit start time).
2. Fixed daily rule: if no Pause/Mittagspause is listed for a day, automatically insert "- Mittagspause 12:00-12:30" into ## Schedule.
3. For duration-only tasks: schedule sequentially into free gaps starting from 09:00, skipping meetings and fixed items. Never overlap. Never schedule past 17:30. Skip Saturday and Sunday entirely.
3a. Daily capacity cap: total scheduled time per day (meetings + pauses + fokus) must not exceed 7.5 hours (450 minutes). If placing a task would exceed the cap, place only what fits; carry the remainder to the next working day.
4. If a task is longer than the largest free gap, split it:
   - remaining = total duration
   - For each free gap: part = min(remaining, gap). Place "Task (1/N) HH:MM-HH:MM". remaining -= part. Stop when 0.
   - Sum of all parts MUST equal original duration exactly.
5. If remaining > 0 after all gaps on current day: carry to next working day as a duration-only line at the top of ## Schedule. Continue until remaining == 0. If no future day exists, add to todo with "(Rest — nächste Woche einplanen)".

## Conflict resolution
- Meetings (prefix "Meeting:") are immovable.
- Pause items are immovable.
- Fokus task overlapping meeting/pause: shift fokus to start after the blocking item. Keep shifting if needed. If it can't fit before 17:30, apply split/carry rules.
- Two meetings overlapping: keep both, add a comment line "> ⚠ Konflikt" after the second one.

Return ONLY the complete updated schedule.md text."""

    model = os.getenv("ABACUS_MODEL", "gpt-5")
    print(f"[Plan] → {model} | {len(body.content)} символов")
    t0 = time.time()

    try:
        client = OpenAI(
            base_url=os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1"),
            api_key=api_key,
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.content},
            ],
        )
        elapsed = time.time() - t0
        raw = response.choices[0].message.content.strip()
        print(f"[Plan] ✓ ответ за {elapsed:.1f}с | {len(raw)} символов")
        # Убираем случайные ```markdown фенсы
        import re
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        # Сохраняем файл
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cleaned = clean_schedule(raw)
        SCHEDULE_FILE.write_text(cleaned, encoding="utf-8")
        print(f"[Plan] ✓ файл сохранён: {SCHEDULE_FILE}")
        return {"ok": True, "content": cleaned}
    except Exception as e:
        print(f"[Plan] ✗ ошибка ({time.time()-t0:.1f}с): {e}")
        raise HTTPException(500, str(e))


# ── Шаг 2: парсинг в JSON для timeline ────────────────────────────────────────

@app.post("/api/parse")
def parse_schedule(body: ParseBody):
    from openai import OpenAI
    import json
    import re
    import time

    api_key = os.getenv("ABACUS_API_KEY")
    if not api_key:
        raise HTTPException(500, "ABACUS_API_KEY не задан в .env")

    system_prompt = """You are a schedule parser. The schedule.md you receive has already been planned — all items have explicit HH:MM-HH:MM time ranges. Your only job is to parse it into JSON for the timeline UI.

## Output format
Return ONLY a raw JSON object — no markdown fences, no explanation:
{"days":[{"date":"YYYY-MM-DD","meetings":[{"time":"HH:MM","title":"string"}],"schedule":[{"title":"string","start":"HH:MM","end":"HH:MM","category":"meeting|pause|fokus","conflict":false}],"todo":[{"text":"string","done":false}]}]}

## Parsing rules
- Parse every line in ## Schedule as {"title", "start", "end", "category"}.
- "conflict": true only if there is a "> ⚠ Konflikt" comment after an item.
- Parse ## Meetings lines (format "HH:MM Title") into the meetings array.
- Parse ## Todo lines: "- [ ] text" → done:false, "- [x] text" → done:true.

## Category rules
- "meeting": title starts with "Meeting:" OR title matches an entry in ## Meetings
- "pause": title contains (case-insensitive): pause, mittagspause, kaffeepause, frei luft, freiluft, break, lunch
- "fokus": everything else

Return ONLY the raw JSON object."""

    model = os.getenv("ABACUS_MODEL", "gpt-5")
    print(f"[Parse] → {model} | {len(body.content)} символов")
    t0 = time.time()

    try:
        client = OpenAI(
            base_url=os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1"),
            api_key=api_key,
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.content},
            ],
        )
        elapsed = time.time() - t0
        raw = response.choices[0].message.content
        print(f"[Parse] ✓ ответ за {elapsed:.1f}с | {len(raw)} символов")
        clean = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "").strip()
        parsed = json.loads(clean)
        if "days" not in parsed:
            raise ValueError("Unexpected JSON shape")
        print(f"[Parse] ✓ распознано дней: {len(parsed['days'])}")
        return parsed
    except json.JSONDecodeError as e:
        print(f"[Parse] ✗ JSON parse error ({time.time()-t0:.1f}с): {e}")
        raise HTTPException(500, f"JSON parse error: {e}")
    except Exception as e:
        print(f"[Parse] ✗ ошибка ({time.time()-t0:.1f}с): {e}")
        raise HTTPException(500, str(e))


# -- Outlook -------------------------------------------------------------------

@app.post("/api/sync/outlook")
def sync_outlook():
    try:
        from outlook_sync import fetch_and_merge
        result = fetch_and_merge(str(SCHEDULE_FILE))
        file_content = SCHEDULE_FILE.read_text(encoding="utf-8") if SCHEDULE_FILE.exists() else ""
        return {"ok": True, "summary": result, "content": file_content}
    except ImportError:
        raise HTTPException(503, "pywin32 nicht installiert -- pip install pywin32")
    except Exception as e:
        raise HTTPException(500, str(e))


# -- Jira ----------------------------------------------------------------------

@app.get("/api/sync/jira")
def sync_jira():
    try:
        from jira_sync import fetch_my_tickets
        return {"ok": True, "tickets": fetch_my_tickets()}
    except Exception as e:
        raise HTTPException(500, str(e))


# -- Enrich --------------------------------------------------------------------

class EnrichBody(BaseModel):
    schedule_content: str

@app.post("/api/enrich")
def enrich(body: EnrichBody):
    try:
        from llm_enricher import enrich_schedule
        return {"ok": True, "enriched": enrich_schedule(body.schedule_content)}
    except Exception as e:
        raise HTTPException(500, str(e))


# -- Add task (natural language) -----------------------------------------------

class TaskBody(BaseModel):
    command: str
    schedule: str

@app.post("/api/task")
def add_task(body: TaskBody):
    from openai import OpenAI
    import time
    import re

    api_key = os.getenv("ABACUS_API_KEY")
    if not api_key:
        raise HTTPException(500, "ABACUS_API_KEY nicht gesetzt in .env")

    from datetime import date
    today = date.today().isoformat()

    system_prompt = (
        f"You are a schedule assistant. Today is {today}.\n"
        "The user gives you a natural-language command to add ONE new task. You receive the current schedule.md and must return the COMPLETE updated file.\n"
        "\n"
        "CRITICAL: You are ONLY allowed to INSERT one new line. Do NOT remove, replace, reorder, or rewrite any existing line. The output must contain every single line from the input, plus exactly one new task line.\n"
        "\n"
        "How to insert:\n"
        "- Parse the command: extract task title, duration (default 30 min), target day (default: today = {today}).\n"
        "- Duration keywords (German/English): '30 Minuten', '1 Stunde', '1h', '2,5 Stunden', '90 Min', etc.\n"
        f"- Day keywords: 'heute'/'today' -> {today}; 'morgen'/'tomorrow' -> next day; weekday names -> nearest upcoming weekday.\n"
        "- Never place tasks on Saturday or Sunday -- move to next Monday if needed.\n"
        "- Find the ## Schedule section of the target day and append the new line at the end of that section, e.g.: '- Aufgabe xyz 30 Minuten'\n"
        "- Before inserting, estimate free time on the target day: sum up durations of existing Schedule entries and subtract from ~7.5h workday (09:00-17:30). If the task duration exceeds remaining free time, move to the next working day (skip Saturday/Sunday). Keep trying until you find a day with enough room or reach 14 days ahead -- then insert anyway on the last tried day.\n"
        "- If the target day block does not exist yet, append a new block at the end of the file:\n"
        "  # YYYY-MM-DD\n\n  ## Meetings\n  _(keine)_\n\n  ## Schedule\n  - TaskName Duration\n\n  ## Todo\n  - [ ] \n"
        "\n"
        "Return ONLY the raw updated schedule.md text -- no explanation, no markdown fences."
    )

    model = os.getenv("ABACUS_MODEL", "gpt-5")
    print(f"[Task] -> {model} | {body.command!r}")
    t0 = time.time()

    try:
        client = OpenAI(
            base_url=os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1"),
            api_key=api_key,
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Команда: {body.command}\n\n--- schedule.md ---\n{body.schedule}"},
            ],
        )
        elapsed = time.time() - t0
        updated = response.choices[0].message.content.strip()
        updated = re.sub(r"```[a-z]*\n?", "", updated).replace("```", "").strip()
        updated = clean_schedule(updated)
        SCHEDULE_FILE.write_text(updated, encoding="utf-8")
        print(f"[Task] ok {elapsed:.1f}s")
        return {"ok": True, "content": updated}
    except Exception as e:
        print(f"[Task] error ({time.time()-t0:.1f}s): {e}")
        raise HTTPException(500, str(e))


# -- UI ------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def ui():
    html_file = Path(__file__).parent / "index.html"
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


# -- Start ---------------------------------------------------------------------

if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    port = find_free_port(int(os.getenv("SERVER_PORT_START", 8100)))
    print(f"\n  Planner -> http://localhost:{port}\n")
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=True)
