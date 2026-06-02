"""
jira_sync.py — читает тикеты из Jira Cloud через GraphQL API
"""
import os
import json
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("JIRA_BASE_URL", "")
USER_EMAIL = os.getenv("JIRA_USERNAME", "")
API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
CLOUD_ID = os.getenv("JIRA_CLOUD_ID", "")

AUTH = HTTPBasicAuth(USER_EMAIL, API_TOKEN)
HEADERS = {
    "Content-Type": "application/json",
    "X-ExperimentalApi": "Atlassian-GraphQL"
}


def fetch_my_tickets(max_results: int = 30) -> list[dict]:
    """
    Возвращает активные тикеты назначенные на тебя через GraphQL API.
    """
    url = f"https://{HOST}/gateway/api/graphql"

    # Твой account ID из Jira
    my_account_id = os.getenv("JIRA_ACCOUNT_ID", "")
    if not my_account_id:
        print("[Jira] ⚠ JIRA_ACCOUNT_ID не задан в .env")
        return []

    jql_query = f"assignee = '{my_account_id}' ORDER BY updated DESC"

    graphql_query = """
    query getMyIssues($cloudId: ID!, $jql: String!) {
      jira {
        issueSearch(cloudId: $cloudId, issueSearchInput: { jql: $jql }, first: 50) @optIn(to: "JiraSpreadsheetComponent-M1") {
          edges {
            node {
              key
              summary
              status {
                name
              }
            }
          }
        }
      }
    }
    """

    payload = {
        "query": graphql_query,
        "variables": {
            "cloudId": CLOUD_ID,
            "jql": jql_query
        }
    }

    print(f"[Jira] GraphQL запрос к {url}")
    response = requests.post(url, json=payload, auth=AUTH, headers=HEADERS, timeout=10)

    if response.status_code != 200:
        print(f"[Jira] Error {response.status_code}: {response.text[:500]}")
        return []

    data = response.json()

    # Проверка на ошибки GraphQL
    if "errors" in data:
        print("[Jira] GraphQL ошибки:")
        print(json.dumps(data["errors"], indent=2, ensure_ascii=False))
        return []

    tickets = []
    issues = data.get("data", {}).get("jira", {}).get("issueSearch", {}).get("edges", [])

    for edge in issues:
        node = edge.get("node", {})
        tickets.append({
            "key": node.get("key", ""),
            "summary": node.get("summary", ""),
            "status": node.get("status", {}).get("name", ""),
            "priority": node.get("priority", {}).get("name", ""),
            "type": node.get("issuetype", {}).get("name", ""),
            "url": f"https://{HOST}/browse/{node.get('key', '')}",
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
