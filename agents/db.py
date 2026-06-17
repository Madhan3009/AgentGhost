import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
from agents.config import DATABASE_URL

print(f"Connecting to database: {DATABASE_URL}")

# Create connection pool
try:
    db_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
except Exception as e:
    print(f"Error initializing connection pool: {e}")
    db_pool = None

@contextmanager
def get_db_connection():
    if not db_pool:
        raise Exception("Database connection pool is not initialized.")
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

@contextmanager
def get_db_cursor(commit=True):
    with get_db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()

def init_db():
    print("Initializing database tables...")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # 1. Enable pgvector
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
            # 2. Create Enums if they don't exist
            cur.execute("""
            DO $$ BEGIN
                CREATE TYPE processing_status AS ENUM ('pending', 'processing', 'completed', 'failed');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """)
            
            cur.execute("""
            DO $$ BEGIN
                CREATE TYPE requirement_status AS ENUM ('pending_review', 'ticket_created', 'conflict_flagged', 'archived');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """)
            
            cur.execute("""
            DO $$ BEGIN
                CREATE TYPE resolution_type AS ENUM ('create_new_ticket', 'exact_match_found', 'contradiction_detected');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """)
            
            # 3. Create tables
            cur.execute("""
            CREATE TABLE IF NOT EXISTS raw_messages (
                id UUID PRIMARY KEY,
                source_channel TEXT,
                author_identity TEXT,
                timestamp TIMESTAMPTZ,
                raw_payload TEXT,
                processing_status processing_status DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            
            cur.execute("""
            CREATE TABLE IF NOT EXISTS extracted_requirements (
                id UUID PRIMARY KEY,
                raw_message_id UUID REFERENCES raw_messages(id) ON DELETE SET NULL,
                extracted_text TEXT,
                is_hard_constraint BOOLEAN,
                confidence_score FLOAT,
                requirement_vector VECTOR(768),
                status requirement_status DEFAULT 'pending_review',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            
            cur.execute("""
            CREATE TABLE IF NOT EXISTS backlog_index (
                id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                ticket_vector VECTOR(768),
                last_synced_at TIMESTAMPTZ,
                external_url TEXT
            );
            """)
            
            cur.execute("""
            CREATE TABLE IF NOT EXISTS reconciliation_actions (
                id UUID PRIMARY KEY,
                requirement_id UUID REFERENCES extracted_requirements(id) ON DELETE CASCADE,
                closest_ticket_id TEXT REFERENCES backlog_index(id) ON DELETE SET NULL,
                similarity_score FLOAT,
                resolution_type resolution_type,
                suggested_ticket_draft JSONB,
                conflict_analysis TEXT,
                human_approved BOOLEAN DEFAULT FALSE,
                approved_by TEXT,
                approved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            
            cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                action_id UUID REFERENCES reconciliation_actions(id) ON DELETE CASCADE,
                actor_jwt_subject TEXT,
                action_payload JSONB,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pr_audits (
                id UUID PRIMARY KEY,
                pr_number INT NOT NULL,
                repo_name TEXT NOT NULL,
                status TEXT NOT NULL,
                diff_snippet TEXT,
                findings JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS dead_letter_log (
                id UUID PRIMARY KEY,
                task_name TEXT NOT NULL,
                task_args TEXT,
                exception TEXT,
                exception_type TEXT,
                traceback TEXT,
                failed_at TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """)
            
            conn.commit()
            print("Database tables initialized successfully.")

if __name__ == "__main__":
    init_db()
