"""
jira_sync.py — читает тикеты из Jira Server/DC
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("JIRA_BASE_URL", "https://jira.your-company.com").rstrip("/")
USERNAME = os.getenv("JIRA_USERNAME", "")
API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
PROJECT = os.getenv("JIRA_PROJECT_KEY", "")
ASSIGNEE = os.getenv("JIRA_ASSIGNEE", USERNAME)


def fetch_my_tickets(max_results: int = 30) -> list[dict]:
    """
    Возвращает активные тикеты назначенные на тебя.
    Статусы: In Progress, In Review, Code Review, To Do
    """
    # Получаем accountId текущего пользователя через /myself
    myself = requests.get(
        f"{BASE_URL}/rest/api/2/myself",
        auth=(USERNAME, API_TOKEN),
        timeout=10,
    )
    myself.raise_for_status()
    account_id = myself.json().get("accountId") or myself.json().get("name") or ASSIGNEE

    jql = (
        f'project = "{PROJECT}" '
        f'AND assignee = "{account_id}" '
        f'AND status in ("In Progress", "In Review", "Code Review", "To Do") '
        f'ORDER BY updated DESC'
    )

    response = requests.get(
        f"{BASE_URL}/rest/api/2/search",
        params={"jql": jql, "maxResults": max_results, "fields": "summary,status,priority,issuetype,updated"},
        auth=(USERNAME, API_TOKEN),
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    tickets = []
    for issue in data.get("issues", []):
        fields = issue.get("fields", {})
        tickets.append({
            "key": issue["key"],
            "summary": fields.get("summary", ""),
            "status": fields.get("status", {}).get("name", ""),
            "priority": fields.get("priority", {}).get("name", ""),
            "type": fields.get("issuetype", {}).get("name", ""),
            "url": f"{BASE_URL}/browse/{issue['key']}",
        })

    return tickets


def tickets_as_todo_lines(tickets: list[dict]) -> list[str]:
    """Форматирует тикеты как строки Todo."""
    lines = []
    for t in tickets:
        status_icon = "🔄" if "Progress" in t["status"] else "👀" if "Review" in t["status"] else "📋"
        lines.append(f"- [ ] {status_icon} [{t['key']}] {t['summary']} ({t['status']})")
    return lines


if __name__ == "__main__":
    tickets = fetch_my_tickets()
    print(f"\n{len(tickets)} тикетов:\n")
    for t in tickets:
        print(f"  {t['key']} [{t['status']}] {t['summary']}")
