"""
Ghost Jira Client — Atlassian Cloud REST API v3
Thin synchronous wrapper; called from Celery tasks (synchronous context).
"""
import base64
import logging
import httpx
from agents import config

logger = logging.getLogger(__name__)

def build_adf_description(text: str) -> dict:
    """Wrap plain text in Atlassian Document Format paragraph node."""
    if not text:
        text = "No description provided."
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}]
            }
        ]
    }

def create_issue(
    title: str,
    description: str,
    priority: str = "Medium",
    labels: list[str] | None = None,
) -> dict:
    """
    POST /rest/api/3/issue
    Returns {"id": "...", "key": "GR-7", "self": "https://..."}
    Raises httpx.HTTPStatusError on failure.
    """
    if not config.JIRA_BASE_URL or not config.JIRA_EMAIL or not config.JIRA_API_TOKEN:
        raise ValueError("Jira configuration variables are incomplete.")

    auth_str = f"{config.JIRA_EMAIL}:{config.JIRA_API_TOKEN}"
    auth_header = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{config.JIRA_BASE_URL.rstrip('/')}/rest/api/3/issue"

    payload = {
        "fields": {
            "project": {
                "key": config.JIRA_PROJECT_KEY
            },
            "summary": title,
            "description": build_adf_description(description),
            "issuetype": {
                "name": config.JIRA_ISSUE_TYPE
            }
        }
    }

    if priority:
        # Standardise first-letter capitalisation for Jira priority (e.g. Medium, High)
        formatted_priority = priority.strip().capitalize()
        payload["fields"]["priority"] = {"name": formatted_priority}

    if labels:
        payload["fields"]["labels"] = labels

    logger.info(f"Sending request to Jira API: {url} for project={config.JIRA_PROJECT_KEY}")
    
    with httpx.Client(timeout=15.0) as client:
        response = client.post(url, json=payload, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Jira API error status={response.status_code} body={response.text}")
            raise e
        
        result = response.json()
        logger.info(f"Jira ticket created successfully: {result.get('key')}")
        return result
