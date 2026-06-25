# Ghost Requirement Agent — End-to-End Demo Guide

This guide details a step-by-step walkthrough for demonstrating the Ghost Requirement Agent. It is organized chronologically, showing how data flows from Slack ingestion to AI processing, human approval, and GitHub PR auditing.

---

## Preparation (Pre-requisites)
Before starting the demo, ensure the backend services are running. Open two terminals and execute:

* **Terminal 1 (FastAPI Server)**:
  ```bash
  make run-api
  ```
* **Terminal 2 (Celery Workers)**:
  ```bash
  make run-workers
  ```

---

## Demo Step 1: System Health Check
**Objective**: Verify the API, database, and background workers are active and healthy.

```bash
curl -s http://localhost:8000/api/health/detailed | python3 -m json.tool
```
* **What to point out**: Show that `database` is `"ok"`, the environment is `"development"`, and `vault` is currently `"disabled"` (falling back to `.env`).

---

## Demo Step 2: Seed the Backlog Index
**Objective**: Populate the mock engineering backlog (Jira tickets) and generate vector embeddings so that the agent can perform similarity searches.

```bash
curl -s -X POST http://localhost:8000/api/test/seed-backlog | python3 -m json.tool
```
* **What to point out**: Explain that 5 mock tickets (regarding login button styling, dark mode, 2FA, session timeout, and Stripe payments) were successfully embedded into `pgvector` for semantic matching.

---

## Demo Step 3: Ingest a New Requirement (Slack Ingestion)
**Objective**: Simulate a PM posting a new requirement in Slack. This triggers the ingestion filter (Agent 1) and similarity search (Agent 2).

```bash
curl -s -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The login button MUST use color #1A73E8 on all mobile views. This is a hard requirement.",
    "channel": "#product-design",
    "user": "priya_pm"
  }' | python3 -m json.tool
```
* **What to point out**: The API responds with `"status": "queued"` immediately (within 2 seconds) to respect Slack webhook timeouts, offloading the heavy AI work to Celery.

---

## Demo Step 4: Verify Extracted Requirement and Get Action ID
**Objective**: Check the dashboard stats and fetch the newly generated action UUID.

1. **Check Dashboard Stats**:
   ```bash
   curl -s http://localhost:8000/api/dashboard/stats | python3 -m json.tool
   ```
   *(You should see `"pendingReview"` or `"newDiscoveries"` increment).*

2. **Fetch the Pending Actions Inbox**:
   ```bash
   curl -s http://localhost:8000/api/dashboard/actions | python3 -m json.tool
   ```
   * **What to point out**: Show that the LLM successfully classified the message as a requirement, generated a Given/When/Then acceptance criteria draft, flagged it as a **hard constraint**, and linked it as a "new discovery".
   * **ACTION REQUIRED**: Copy the `"id"` field from this response (e.g. `43d5d1a1-77c1-4894-b01d-f8d812d2e820`). This is your `<action_id>`.

---

## Demo Step 5: Ingest a Contradictory Requirement
**Objective**: Demonstrate how the Backlog Reconciler (Agent 2) and Conflict Resolver (Agent 3) block contradictory requirements.
*(Note: Ticket `PROJ-104` has a session timeout of 30 minutes. We will propose 40 minutes).*

```bash
curl -s -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hey team, we need to change the session timeout to 40 minutes of inactivity.",
    "channel": "#security",
    "user": "dave_sec"
  }' | python3 -m json.tool
```

Wait 5 seconds for Celery processing, then fetch the inbox again:
```bash
curl -s http://localhost:8000/api/dashboard/actions | python3 -m json.tool
```
* **What to point out**: Show that this requirement was flagged as a `contradiction_alert` instead of a new discovery. The AI comparison highlights that the new request (40 minutes) directly conflicts with the existing ticket `PROJ-104` (30 minutes).

---

## Demo Step 6: Authenticate as Reviewer (JWT)
**Objective**: Authenticate a reviewer to obtain a JWT token, enforcing the requirement that only authorized users can approve changes.

```bash
curl -s -X POST http://localhost:8000/api/auth/token \
  -F "username=admin" \
  -F "password=ghostadmin" | python3 -m json.tool
```
* **What to point out**: Highlight that this returns a signed JWT. Copy the value of `"access_token"`.

---

## Demo Step 7: Human Approval Gate (Mutation)
**Objective**: Approve the "new discovery" requirement we got in **Step 4** (using the token from **Step 6**). This triggers the Jira ticket mock write.

```bash
# REPLACE <action_id> with the UUID from Step 4
# REPLACE <JWT_TOKEN> with the access_token from Step 6
curl -s -X POST http://localhost:8000/api/dashboard/actions/<action_id>/approve \
  -H "Authorization: Bearer <JWT_TOKEN>" | python3 -m json.tool
```
* **What to point out**: The API responds with `"status": "approved_queued"`. It will write the new ticket to the database backlog and log an immutable entry in the `audit_log` table.

---

## Demo Step 8: Verify Approval Audit Log
**Objective**: Show that the approval has been locked in the database.

Log into the PostgreSQL database container to view the immutable audit trail:
```bash
docker exec -it ghost-postgres psql -U ghost -d ghost_poc -c "SELECT actor_jwt_subject, action_payload FROM audit_log;"
```
* **What to point out**: Point out the JSON log payload showing who approved the ticket, when it was approved, and the generated Jira ticket ID (e.g. `GHOST-4235`).

---

## Demo Step 9: Pull Request Auditing (Phase 4 Security Guard)
**Objective**: Demonstrate how the agent monitors developers pushing code that violates approved requirements. We will simulate a GitHub PR that tries to change the session timeout to 40 minutes (violating the approved security constraints).

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

Wait 5 seconds, then check the PostgreSQL database to view the PR Audit findings:
```bash
docker exec -it ghost-postgres psql -U ghost -d ghost_poc -c "SELECT pr_number, status, findings FROM pr_audits;"
```
* **What to point out**: The status will show `"failed"` or `"violation_detected"`, listing the specific security constraint that was violated (the session timeout policy).

---

## Demo Step 10: Inspecting the Database (Postgres & pgvector)
**Objective**: Directly query the PostgreSQL database and demonstrate vector similarity matching using `pgvector`.

### 1. Connect to PostgreSQL
Open an interactive `psql` shell in the database container:
```bash
docker exec -it ghost-postgres psql -U ghost -d ghost_poc
```

### 2. Basic Schema & Tables Inspection
Run these commands inside the `psql` shell:
* **List all tables**:
  ```sql
  \dt
  ```
* **View raw Slack messages**:
  ```sql
  SELECT id, source_channel, author_identity, processing_status FROM raw_messages;
  ```
* **View extracted requirements**:
  ```sql
  SELECT id, extracted_text, is_hard_constraint, status FROM extracted_requirements;
  ```
* **View reconciliation inbox**:
  ```sql
  SELECT id, closest_ticket_id, similarity_score, resolution_type, human_approved FROM reconciliation_actions;
  ```

### 3. Query pgvector Embeddings & Perform Semantic Search
To find the closest mock Jira ticket in `backlog_index` to a specific extracted requirement, you can run a **cosine similarity** query directly in SQL using the `<=>` operator (cosine distance):

* **View vector representation** (shows dimensions & a snippet of the vector):
  ```sql
  SELECT id, title, ticket_vector FROM backlog_index LIMIT 1;
  ```

* **Perform a manual semantic search query**:
  This query calculates the cosine similarity (defined as `1 - cosine_distance`) between the vector of your first extracted requirement and the embedded mock Jira tickets in the backlog index:
  ```sql
  SELECT 
      b.id AS ticket_id, 
      b.title, 
      (1 - (b.ticket_vector <=> r.requirement_vector)) AS similarity
  FROM backlog_index b, extracted_requirements r
  WHERE r.id = (SELECT id FROM extracted_requirements LIMIT 1)
  ORDER BY similarity DESC
  LIMIT 3;
  ```

* **Exit psql shell**:
  ```sql
  \q
  ```
