# Ghost Requirement Agent

Ghost Requirement Agent is an autonomous pipeline designed to capture, reconcile, and manage product requirements from communication channels like Slack and Microsoft Teams. It leverages Agno agents powered by Gemini, PostgreSQL with pgvector for semantic search, and a FastAPI/Celery architecture with a Next.js dashboard for human-in-the-loop approvals.

## System Overview

The system listens to communication channels, extracts formal product requirements, checks them against your existing Jira/Linear backlog using vector similarity, and flags contradictions or drafts new tickets for human approval.

It is composed of the following core components:
1. **FastAPI Backend**: Handles webhooks, API endpoints, and orchestrates the database.
2. **Celery Workers**: Background workers that execute the AI logic using Agno Agents.
3. **PostgreSQL + pgvector**: Stores raw messages, extracted requirements, vector embeddings, and audit logs.
4. **Redis**: Acts as the message broker for Celery and handles the Dead Letter Queue.
5. **Next.js Dashboard**: A user interface for product managers to review, approve, or dismiss AI-generated actions.
6. **HashiCorp Vault** (Optional): Provides secure, centralized secret management.

---

## Setup & Installation

### Prerequisites
- Docker and Docker Compose (for PostgreSQL, Redis, and Vault)
- Node.js 18+ (for the Next.js dashboard)
- Python 3.12+ (for the backend environment)

### Environment Configuration
1. Copy the sample environment file to create your local configuration:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and add your Gemini API Key:
   ```env
   GEMINI_API_KEY=your_actual_gemini_api_key_here
   ```
   *(All other default values are pre-configured for local Docker development).*

### Dependency Installation
Install the required dependencies for both the backend and frontend:
```bash
# Backend (ensure your virtual environment is active)
make install

# Frontend
cd app && npm install && cd ..
```

---

## Running the Application

The project includes a `Makefile` to simplify running the necessary services.

### 1. Start Infrastructure & Initialize Database
Start PostgreSQL and Redis in Docker, then initialize the database tables:
```bash
make up
sleep 5
make init-db
```

### 2. Boot the Services
You must run the API, Background Workers, and the Frontend Dashboard simultaneously. It is recommended to use separate terminal windows.

**Terminal 1 (FastAPI Server):**
```bash
make run-api
```
*The API documentation will be available at: http://localhost:8000/api/docs*

**Terminal 2 (Celery Workers):**
```bash
make run-workers
```

**Terminal 3 (Next.js Dashboard):**
```bash
make run-dashboard
```
*The Dashboard will be available at: http://localhost:3000/dashboard*

---

## Testing the Workflows

The application includes built-in endpoints to simulate real-world inputs. You can run these commands from a separate terminal.

### Seed the Backlog
Before testing, populate the database with mock Jira tickets to provide the AI with context.
```bash
curl -sf -X POST http://localhost:8000/api/test/seed-backlog | python3 -m json.tool
```

### Scenario 1: Submitting a New Requirement
Simulate a Slack message containing a new requirement that does not conflict with existing tickets.
```bash
curl -sf -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{"text": "We need to add a Dark Mode toggle to the user profile page. This is required for the upcoming accessibility audit.", "channel": "#product-design", "user": "sarah_ux"}' | python3 -m json.tool
```
*Result:* The AI drafts a new Jira ticket. You can review and approve this "New Discovery" on the Dashboard.

### Scenario 2: Submitting a Contradictory Requirement
Simulate a Slack message that conflicts with an existing requirement.
```bash
curl -sf -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{"text": "The login button MUST use color #1A73E8 on all mobile views. This is a hard requirement.", "channel": "#product", "user": "priya_pm"}' | python3 -m json.tool
```
*Result:* The AI detects a conflict with an existing ticket. A "Contradiction Alert" will appear on the Dashboard with a side-by-side comparison.

### Scenario 3: Noise Filtering
Simulate casual chatter that should be ignored by the system.
```bash
curl -sf -X POST http://localhost:8000/api/test/mock-slack-message \
  -H "Content-Type: application/json" \
  -d '{"text": "Good morning everyone! Excited for the sprint review today.", "channel": "#general", "user": "john_eng"}' | python3 -m json.tool
```
*Result:* The ingestion agent classifies this as non-requirement noise. It is dropped, and no action appears on the dashboard.

### Scenario 4: Pull Request Safeguard
Simulate a GitHub Pull Request that violates an existing documented requirement.
```bash
make test-pr
```
*Result:* The agent audits the code diff against the vector database and records the violation in the `pr_audits` database table.

---

## Dashboard Usage

1. Navigate to http://localhost:3000/dashboard
2. Review the list of Pending Actions resulting from your ingested messages.
3. Click **Approve** on a "New Discovery" card to convert it into an official backlog item.
4. Click **Dismiss** to reject an action.

---

## Production Security (HashiCorp Vault)

For production environments, the system supports HashiCorp Vault for secure secret management instead of relying on `.env` files.

To test Vault locally:
1. Start the Vault Dev Server:
   ```bash
   make run-vault
   ```
2. Seed your `.env` secrets into the Vault:
   ```bash
   make seed-vault
   ```
3. Restart your API and Workers with Vault enabled:
   ```bash
   VAULT_ENABLED=true make run-api
   VAULT_ENABLED=true make run-workers
   ```

---

## Monitoring & Administration

- **API Documentation (Swagger UI)**: http://localhost:8000/api/docs
- **Celery Flower (Task Monitoring)**: Start via `make run-flower` and view at http://localhost:5555
- **System Health Check**: 
  ```bash
  curl http://localhost:8000/api/health/detailed | python3 -m json.tool
  ```

### Stopping the Environment
To gracefully stop all Docker containers and clean up Python caches:
```bash
make down
make clean
```
