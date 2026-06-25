import json
import pytest
from unittest.mock import MagicMock, patch
from agents.tasks import approve_action_task


@pytest.fixture
def mock_db_cursor(mocker):
    # Mock context manager get_db_cursor
    mock_cursor = MagicMock()
    
    # We patch agents.tasks.get_db_cursor context manager
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_cursor
    mocker.patch("agents.tasks.get_db_cursor", return_value=mock_ctx)
    return mock_cursor


def test_approve_action_not_found(mock_db_cursor):
    # Fetchone returns None (action not found)
    mock_db_cursor.fetchone.return_value = None

    res = approve_action_task(action_id="fake-action-id", approved_by_user="tester")
    assert res == {"status": "skipped", "reason": "action_not_found"}
    mock_db_cursor.execute.assert_called_once()


def test_approve_action_already_approved(mock_db_cursor):
    # Fetchone returns row where human_approved=True
    mock_db_cursor.fetchone.return_value = {
        "resolution_type": "create_new_ticket",
        "suggested_ticket_draft": {"title": "A Ticket"},
        "human_approved": True,
        "requirement_id": "req-id-123",
        "extracted_text": "text content",
        "requirement_vector": [0.1, 0.2]
    }

    res = approve_action_task(action_id="action-id-123", approved_by_user="tester")
    assert res == {"status": "skipped", "reason": "already_approved"}


@patch("agents.jira_client.create_issue")
@patch("agents.config.JIRA_BASE_URL", "https://test-site.atlassian.net")
def test_approve_action_success(mock_create_issue, mock_db_cursor):
    # Mock jira client response
    mock_create_issue.return_value = {
        "id": "10001",
        "key": "GR-33",
        "self": "https://test-site.atlassian.net/rest/api/3/issue/10001"
    }

    # Fetchone returns row with valid data to approve
    mock_db_cursor.fetchone.return_value = {
        "resolution_type": "create_new_ticket",
        "suggested_ticket_draft": {
            "title": "Build Dark Mode Toggle",
            "description": "As a user...",
            "acceptanceCriteria": ["AC1", "AC2"],
            "priority": "High"
        },
        "human_approved": False,
        "requirement_id": "req-id-123",
        "extracted_text": "Original extracted requirement text",
        "requirement_vector": [0.1] * 768
    }

    res = approve_action_task(action_id="action-id-123", approved_by_user="tester")

    assert res["status"] == "approved"
    assert res["jira_ticket_id"] == "GR-33"
    assert res["title"] == "Build Dark Mode Toggle"

    # Verify that create_issue was called with correct parameters
    mock_create_issue.assert_called_once_with(
        title="Build Dark Mode Toggle",
        description="As a user...\n\n**Acceptance Criteria:**\n- AC1\n- AC2",
        priority="High",
        labels=["ghost-agent", "auto-generated"]
    )

    # Verify database updates were triggered (backlog_index, reconciliation_actions, extracted_requirements, audit_log)
    # The cursor was executed at least 5 times (1 SELECT + 4 writes)
    assert mock_db_cursor.execute.call_count >= 5
