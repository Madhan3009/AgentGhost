import base64
import pytest
import httpx
from unittest.mock import MagicMock, patch
from agents import config
from agents.jira_client import build_adf_description, create_issue


def test_build_adf_description():
    # Test normal text
    adf = build_adf_description("Hello Jira")
    assert adf["version"] == 1
    assert adf["type"] == "doc"
    assert adf["content"][0]["type"] == "paragraph"
    assert adf["content"][0]["content"][0]["text"] == "Hello Jira"

    # Test empty text fallback
    adf_empty = build_adf_description("")
    assert adf_empty["content"][0]["content"][0]["text"] == "No description provided."


@patch("agents.config.JIRA_BASE_URL", "https://test-site.atlassian.net")
@patch("agents.config.JIRA_EMAIL", "test-user@test.com")
@patch("agents.config.JIRA_API_TOKEN", "mock-token-123")
@patch("agents.config.JIRA_PROJECT_KEY", "TEST")
@patch("agents.config.JIRA_ISSUE_TYPE", "Story")
def test_create_issue_success(mocker):
    # Mock httpx.Client
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "id": "10001",
        "key": "TEST-12",
        "self": "https://test-site.atlassian.net/rest/api/3/issue/10001"
    }
    
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    mocker.patch("httpx.Client", return_value=mock_client)

    result = create_issue(
        title="Test Story Title",
        description="Test Story Description",
        priority="High",
        labels=["test-label"],
    )

    # Check return values
    assert result["key"] == "TEST-12"
    assert result["id"] == "10001"

    # Check post payload parameters
    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    
    url = args[0]
    payload = kwargs["json"]
    headers = kwargs["headers"]

    assert url == "https://test-site.atlassian.net/rest/api/3/issue"
    assert payload["fields"]["project"]["key"] == "TEST"
    assert payload["fields"]["summary"] == "Test Story Title"
    assert payload["fields"]["issuetype"]["name"] == "Story"
    assert payload["fields"]["priority"]["name"] == "High"
    assert "test-label" in payload["fields"]["labels"]

    # Verify authorization header encoding
    expected_auth = base64.b64encode(b"test-user@test.com:mock-token-123").decode("utf-8")
    assert headers["Authorization"] == f"Basic {expected_auth}"


@patch("agents.config.JIRA_BASE_URL", "")
def test_create_issue_missing_config():
    # If base url is empty, it should raise ValueError
    with pytest.raises(ValueError, match="Jira configuration variables are incomplete"):
        create_issue("Title", "Desc")


@patch("agents.config.JIRA_BASE_URL", "https://test-site.atlassian.net")
@patch("agents.config.JIRA_EMAIL", "test-user@test.com")
@patch("agents.config.JIRA_API_TOKEN", "mock-token-123")
@patch("agents.config.JIRA_PROJECT_KEY", "TEST")
def test_create_issue_http_error(mocker):
    # Mock HTTP status error
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Bad Request",
        request=MagicMock(),
        response=mock_response
    )

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    mocker.patch("httpx.Client", return_value=mock_client)

    with pytest.raises(httpx.HTTPStatusError):
        create_issue("Title", "Desc")
