# How to Run the Phase 0 (PoC) Presentation Scripts

All scripts are located in the `poc/` directory and use a local Docker stack for Postgres and Redis, alongside the Gemini API.

## Step 1: Start the Infrastructure
Before running the database or queue scripts, ensure the Docker containers are running:
```bash
cd ~/Desktop/Ghost/poc
docker compose up -d
```
*(You can verify they are running with `docker compose ps`)*

---

## Script 1: Gemini AI (Text Generation & Embeddings)
This script demonstrates taking a raw slack message, extracting a structured JSON requirement using `gemini-3.5-flash`, and converting it into a vector array using `gemini-embedding-2`.

**How to run:**
```bash
cd ~/Desktop/Ghost/poc
node test-gemini.js
```
*(Note: It automatically loads your API key from the `.env` file).*

---

## Script 2: Vector Database (PostgreSQL + pgvector)
This script connects to the local Postgres container, enables the `pgvector` extension, inserts mock vector data (simulating output from Gemini), and performs a mathematical **cosine distance similarity search**.

**How to run:**
```bash
cd ~/Desktop/Ghost/poc
node test-pgvector.js
```
*Expected Output: It will show that a target vector is 99.74% similar to the "Login button color" item.*

---

## Script 3: Asynchronous Queues (Redis + BullMQ)
This script demonstrates how Agent 1, Agent 2, and Agent 3 will reliably pass data to each other. It creates a Publisher that adds a job to a Redis queue, and a background Worker that picks it up, simulates a delay (like an LLM call), and marks it complete.

**How to run:**
```bash
cd ~/Desktop/Ghost/poc
node test-bullmq.js
```
*Expected Output: You will see the job being added to the queue, picked up by the worker, and successfully completed.*

---

## Teardown (After Presentation)
When you are done, you can stop the background databases so they don't consume memory:
```bash
cd ~/Desktop/Ghost/poc
docker compose down
```