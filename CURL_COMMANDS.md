# Ghost Requirement Agent API — Curl Commands

This document contains a comprehensive list of curl commands available for testing, debugging, and interacting with the Ghost Requirement Agent API (FastAPI server running on `http://localhost:8000`).

---

## Table of Contents
1. [Authentication](#1-authentication)
2. [Health & System Checks](#2-health--system-checks)
3. [Developer Testing Utilities](#3-developer-testing-utilities)
4. [Dashboard & API Endpoints](#4-dashboard--api-endpoints)
5. [Webhook Receivers](#5-webhook-receivers)

---

## 1. Authentication

### Get JWT Access Token
Authenticates a reviewer and returns an HS256 JWT bearer token (valid for 60 minutes).
* **Endpoint**: `POST /api/auth/token`
* **Content-Type**: `application/x-www-form-urlencoded`
* **Credentials (Dev)**: `username=admin` / `password=ghostadmin`

```bash
curl -s -X POST http://localhost:8000/api/auth/token \
  -F "username=admin" \
  -F "password=ghostadmin" | python3 -m json.tool
```

**Response Example:**
```json
{
    "access_token": "eyJhbGciOiJIUzI1...",
    "token_type": "bearer"
}
```

---

## 2. Health & System Checks

### Liveness Health Check
Verifies that the API service is up and running.
* **Endpoint**: `GET /api/health`

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

### Detailed Health Check
Verifies database connection status, total raw messages, extracted requirements, and Vault integration status.
* **Endpoint**: `GET /api/health/detailed`

```bash
curl -s http://localhost:8000/api/health/detailed | python3 -m json.tool
```

---

## 3. Developer Testing Utilities

### Seed Backlog Index
Pre-seeds the `backlog_index` with mock Jira tickets (e.g. `PROJ-101` to `PROJ-105`) and generates vector embeddings for semantic search.
* **Endpoint**: `POST /api/test/seed-backlog`

```bash
curl -s -X POST http://localhost:8000/api/test/seed-backlog | python3 -m json.tool
```

### Inject Mock Slack Message
Simulates an incoming Slack message. This bypasses signature checking and directly triggers the requirement extraction pipeline.
* **Endpoint**: `POST /api/test/mock-slack-message`
* **Payload Fields**:
  * `text` (String): The message contents.
  * `channel` (String, Optional): Source Slack channel. Default: `#product-design`.
  * `user` (String, Optional): Sender user ID. Default: `U_MOCK_USER`.

```bash
curl -s -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The login button MUST use color #1A73E8 on all mobile views. This is a hard requirement.",
    "channel": "#product-design",
    "user": "priya_pm"
  }' | python3 -m json.tool
```

### Inject Mock GitHub PR Webhook
Simulates a GitHub pull request event. Enqueues a PR diff analysis task directly.
* **Endpoint**: `POST /api/test/mock-github-pr`
* **Payload Fields**:
  * `pr_number` (Integer): The pull request number.
  * `repo_name` (String): The GitHub repository.
  * `title` (String): Title of the PR.
  * `diff_text` (String): The pull request diff text to analyze.

```bash
curl -s -X POST http://localhost:8000/api/test/mock-github-pr \
  -H "Content-Type: application/json" \
  -d '{
    "pr_number": 42,
    "repo_name": "acme/web-app",
    "title": "Increase session timeout to 40 mins",
    "diff_text": "diff --git a/src/config/session.js b/src/config/session.js\nindex 83a2d78..d3210ef\n--- a/src/config/session.js\n+++ b/src/config/session.js\n@@ -10,3 +10,3 @@\n-  sessionTimeout: 15 * 60 * 1000,\n+  sessionTimeout: 40 * 60 * 1000,"
  }' | python3 -m json.tool
```

---

## 4. Dashboard & API Endpoints

### Get Dashboard Statistics
Returns aggregated counts (Pending Review, New Discoveries, Contradictions, Resolved, Processed Today) for dashboard UI display.
* **Endpoint**: `GET /api/dashboard/stats`

```bash
curl -s http://localhost:8000/api/dashboard/stats | python3 -m json.tool
```

### Get Pending Reconciliation Actions
Fetches up to 50 active reconciliation actions waiting for human-in-the-loop review.
* **Endpoint**: `GET /api/dashboard/actions`

```bash
curl -s http://localhost:8000/api/dashboard/actions | python3 -m json.tool
```

### Get Recent Ingested Messages
Fetches the last 20 raw Slack/Teams messages and their current ingestion pipeline processing status.
* **Endpoint**: `GET /api/dashboard/messages`

```bash
curl -s http://localhost:8000/api/dashboard/messages | python3 -m json.tool
```

### Sync Backlog
Triggers a manual sync of the backlog index (calls seed backlog).
* **Endpoint**: `POST /api/dashboard/sync`

```bash
curl -s -X POST http://localhost:8000/api/dashboard/sync | python3 -m json.tool
```

### Approve Action (Requires Auth)
Approves a pending reconciliation action. Enqueues a Jira creation task + immutability audit logger.
* **Endpoint**: `POST /api/dashboard/actions/{action_id}/approve`
* **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`

```bash
# Replace <action_id> and <ACCESS_TOKEN> before running
curl -s -X POST http://localhost:8000/api/dashboard/actions/<action_id>/approve \
  -H "Authorization: Bearer <ACCESS_TOKEN>" | python3 -m json.tool
```

### Dismiss/Archive Action (Requires Auth)
Dismisses a pending reconciliation action, flagging the extracted requirement as `archived`.
* **Endpoint**: `POST /api/dashboard/actions/{action_id}/dismiss`
* **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`

```bash
# Replace <action_id> and <ACCESS_TOKEN> before running
curl -s -X POST http://localhost:8000/api/dashboard/actions/<action_id>/dismiss \
  -H "Authorization: Bearer <ACCESS_TOKEN>" | python3 -m json.tool
```

---

## 5. Webhook Receivers

These endpoints receive real production webhooks from external providers. They enforce signature validation if signing secrets are configured.

### Slack Webhook Receiver
Receives production Slack event subscriptions.
* **Endpoint**: `POST /api/webhooks/slack`
* **Headers (Required for verification)**:
  * `X-Slack-Request-Timestamp: <timestamp>`
  * `X-Slack-Signature: <computed-signature>`

```bash
# General test payload (Slack URL verification challenge)
curl -s -X POST http://localhost:8000/api/webhooks/slack \
  -H "Content-Type: application/json" \
  -d '{
    "type": "url_verification",
    "challenge": "sample_challenge_token"
  }'
```

### MS Teams Webhook Receiver
Receives production MS Teams incoming webhook payloads.
* **Endpoint**: `POST /api/webhooks/teams`

```bash
curl -s -X POST http://localhost:8000/api/webhooks/teams \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The login page requires a loading spinner when authenticating.",
    "from": {
      "id": "teams_user_1",
      "name": "Jane Doe"
    },
    "channelData": {
      "channel": {
        "name": "teams-general"
      }
    }
  }'
```

### GitHub Webhook Receiver
Receives unified diff pull request updates.
* **Endpoint**: `POST /api/webhooks/github`
* **Headers**:
  * `X-Hub-Signature-256: sha256=<signature>`
  * `X-GitHub-Event: pull_request`

```bash
curl -s -X POST http://localhost:8000/api/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -d '{
    "action": "opened",
    "number": 101,
    "repository": {
      "full_name": "acme/web-app"
    },
    "pull_request": {
      "title": "Add session timeout override",
      "diff_url": "https://github.com/acme/web-app/pull/101.diff"
    }
  }'
```
