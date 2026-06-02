"""
outlook_sync.py — читает митинги из Outlook через win32com
и мержит их в schedule.md
"""
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CALENDAR_NAME = os.getenv("OUTLOOK_CALENDAR_NAME", "Kalender")
DAYS_AHEAD = int(os.getenv("OUTLOOK_DAYS_AHEAD", 7))
SCHEDULE_FILE = Path(os.getenv("SCHEDULE_FILE", "schedule.md"))


def get_outlook_meetings(days_ahead: int = DAYS_AHEAD) -> dict[str, list[dict]]:
    """
    Возвращает словарь {date_str: [{"time": "HH:MM", "end": "HH:MM", "title": str, "duration_min": int}]}
    """
    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")

    # Ищем нужный календарь
    calendar = None
    for folder in namespace.Folders:
        for sub in folder.Folders:
            if sub.Name == CALENDAR_NAME:
                calendar = sub
                break
        if calendar:
            break

    if calendar is None:
        # fallback — дефолтный календарь
        calendar = namespace.GetDefaultFolder(9)  # 9 = olFolderCalendar

    # Диапазон дат — жёсткая граница, применяется и в Restrict и при итерации
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days_ahead)

    start_str = start.strftime("%m/%d/%Y %H:%M %p")
    end_str = end.strftime("%m/%d/%Y %H:%M %p")

    items = calendar.Items
    items.IncludeRecurrences = True
    items.Sort("[Start]")
    # Restrict — подсказка для Outlook, но для повторяющихся событий ненадёжна
    restriction = f"[Start] >= '{start_str}' AND [Start] <= '{end_str}'"
    restricted = items.Restrict(restriction)

    meetings_by_day: dict[str, list[dict]] = {}

    print(f"[Outlook] Календарь: '{calendar.Name}'")
    print(f"[Outlook] Диапазон: {start.date()} — {end.date()} ({days_ahead} дней)")

    raw_count = 0
    skipped_oof = 0
    skipped_range = 0
    skipped_error = 0

    for item in restricted:
        raw_count += 1
        try:
            title = item.Subject or "(kein Titel)"
            busy = item.BusyStatus  # 0=Free, 1=Tentative, 2=Busy, 3=OOF, 4=WorkingElsewhere

            if busy == 3:
                print(f"[Outlook]   SKIP (OOF): {title}")
                skipped_oof += 1
                continue

            dt_start: datetime = item.Start
            dt_end: datetime = item.End

            # Жёсткая проверка диапазона — защита от recurring-событий вне окна
            try:
                # pywintypes.datetime совместим с datetime для сравнения
                if dt_start < start or dt_start >= end:
                    skipped_range += 1
                    continue
            except Exception:
                pass  # если сравнение не работает — пропускаем проверку

            day_key = dt_start.strftime("%Y-%m-%d")
            duration = int((dt_end - dt_start).total_seconds() / 60)

            print(f"[Outlook]   + {day_key} {dt_start.strftime('%H:%M')}-{dt_end.strftime('%H:%M')} ({duration}min) | {title}")

            meetings_by_day.setdefault(day_key, []).append({
                "time": dt_start.strftime("%H:%M"),
                "end": dt_end.strftime("%H:%M"),
                "title": title,
                "duration_min": duration,
            })
        except Exception as e:
            print(f"[Outlook]   ERROR при чтении события: {e}")
            skipped_error += 1
            continue

    print(f"[Outlook] Итого: {raw_count} событий от Outlook, вне окна: {skipped_range}, OOF: {skipped_oof}, ошибок: {skipped_error}")
    print(f"[Outlook] Добавлено в расписание: {sum(len(v) for v in meetings_by_day.values())} митингов по {len(meetings_by_day)} дням")

    return meetings_by_day


def parse_schedule_md(content: str) -> dict[str, dict]:
    """Парсит schedule.md в словарь {date: {meetings_block, schedule_block, todo_block, raw}}"""
    days = {}
    # Разбиваем по заголовкам дней
    parts = re.split(r"\n(?=# \d{4}-\d{2}-\d{2})", content.strip())
    for part in parts:
        m = re.match(r"# (\d{4}-\d{2}-\d{2})", part)
        if m:
            days[m.group(1)] = part.strip()
    return days


def build_day_block(date_str: str, meetings: list[dict], existing: str | None = None) -> str:
    """
    Строит или обновляет блок дня — вставляет митинги из Outlook.
    Существующие вручную добавленные записи сохраняются.
    """
    # Извлекаем существующие секции
    existing_meetings_lines: list[str] = []
    existing_schedule_lines: list[str] = []
    existing_todo_lines: list[str] = []

    if existing:
        sections = re.split(r"## (Meetings|Schedule|Todo)", existing)
        for i, sec in enumerate(sections):
            if sec == "Meetings" and i + 1 < len(sections):
                existing_meetings_lines = [l for l in sections[i+1].strip().splitlines() if l.strip().startswith("-")]
            elif sec == "Schedule" and i + 1 < len(sections):
                existing_schedule_lines = [l for l in sections[i+1].strip().splitlines() if l.strip().startswith("-")]
            elif sec == "Todo" and i + 1 < len(sections):
                existing_todo_lines = [l for l in sections[i+1].strip().splitlines() if l.strip().startswith("-")]

    # Строим Meetings секцию — мержим Outlook + существующие вручную
    merged_meetings = list(existing_meetings_lines)
    for m in meetings:
        line = f"- {m['time']} {m['title']}"
        # Добавляем только если такой же (время + название) ещё нет
        meeting_sig = f"{m['time']} {m['title']}"
        if not any(meeting_sig in l for l in merged_meetings):
            merged_meetings.append(line)
    # Сортируем по времени
    def meeting_sort_key(line):
        match = re.match(r"- (\d{2}:\d{2})", line)
        return match.group(1) if match else "99:99"
    merged_meetings.sort(key=meeting_sort_key)

    # Строим Schedule секцию — добавляем митинги из Outlook если их ещё нет
    # Митинги из календаря всегда с префиксом "Meeting:"
    merged_schedule = list(existing_schedule_lines)
    for m in meetings:
        title = m["title"] if m["title"].startswith("Meeting:") else f"Meeting: {m['title']}"
        line = f"- {title} {m['time']}-{m['end']}"
        # Проверяем по уникальной сигнатуре (время + название)
        schedule_sig = f"{m['time']}-{m['end']} {m['title']}"
        if not any(m["title"] in l and m["time"] in l and m["end"] in l for l in merged_schedule):
            merged_schedule.append(line)
    # Сортируем по времени начала
    def schedule_sort_key(line):
        match = re.search(r"(\d{2}:\d{2})-", line)
        return match.group(1) if match else "99:99"
    merged_schedule.sort(key=schedule_sort_key)

    meetings_block = "\n".join(merged_meetings) if merged_meetings else "_(keine)_"
    schedule_block = "\n".join(merged_schedule) if merged_schedule else "_(leer)_"
    todo_block = "\n".join(existing_todo_lines) if existing_todo_lines else "- [ ] "

    return f"""# {date_str}

## Meetings
{meetings_block}

## Schedule
{schedule_block}

## Todo
{todo_block}"""


def fetch_and_merge(schedule_file: str = None) -> str:
    """
    Основная функция: читает Outlook, мержит в schedule.md, сохраняет.
    Сохраняет только дни в окне сегодня + DAYS_AHEAD.
    Возвращает текстовый summary.
    """
    path = Path(schedule_file or SCHEDULE_FILE)
    existing_content = path.read_text(encoding="utf-8") if path.exists() else ""

    meetings_by_day = get_outlook_meetings()
    existing_days = parse_schedule_md(existing_content)

    # Окно дат: сегодня + DAYS_AHEAD
    today = datetime.now().date()
    allowed_dates = {
        (today + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(DAYS_AHEAD + 1)
    }
    print(f"[Outlook] Окно сохранения: {today} — {(today + timedelta(days=DAYS_AHEAD))}")
    print(f"[Outlook] allowed_dates: {sorted(allowed_dates)}")
    print(f"[Outlook] meetings_by_day keys: {sorted(meetings_by_day.keys())}")

    # Обновляем/создаём блоки только для дней в окне
    matched = []
    for date_str, meetings in meetings_by_day.items():
        if date_str in allowed_dates:
            matched.append(date_str)
            existing_days[date_str] = build_day_block(date_str, meetings, existing_days.get(date_str))
        else:
            print(f"[Outlook] SKIP (вне окна): {date_str} ({len(meetings)} митингов)")

    print(f"[Outlook] Совпало с окном: {sorted(matched)}")

    # Сохраняем только дни внутри окна (старые дни отбрасываем)
    sorted_dates = sorted(d for d in existing_days.keys() if d in allowed_dates)
    print(f"[Outlook] Будет записано дней: {sorted_dates}")
    new_content = "\n\n---\n\n".join(existing_days[d] for d in sorted_dates)

    print(f"[Outlook] Пишем в файл: {path} ({len(new_content)} символов)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")
    print(f"[Outlook] Файл записан ✓")

    total = sum(len(v) for k, v in meetings_by_day.items() if k in allowed_dates)
    print(f"[Outlook] Сохранено дней: {len(sorted_dates)}, митингов: {total}")
    return f"Синхронизировано {total} митингов по {len(sorted_dates)} дням"


if __name__ == "__main__":
    print(fetch_and_merge())
