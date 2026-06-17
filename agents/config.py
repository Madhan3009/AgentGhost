"""
Ghost Requirement Agent - Configuration Module
Loads configuration from environment variables with sensible defaults.

Secret resolution priority (highest → lowest):
  1. HashiCorp Vault KV v2  (when VAULT_ENABLED=true)
  2. Environment variables / .env file
  3. Hardcoded development defaults

Set VAULT_ENABLED=true and configure VAULT_ADDR / VAULT_TOKEN (or use
Kubernetes auth) to enable centralised secret management in production.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# Environment loading: Root .env > poc/.env (priority: first loaded wins)
# ─────────────────────────────────────────────────────────────────────────────

_root_dir = Path(__file__).resolve().parent.parent
_env_path = _root_dir / ".env"
_poc_env_path = _root_dir / "poc" / ".env"

# Load root .env first (higher priority)
if _env_path.exists():
    load_dotenv(_env_path, override=False)
    
# Fall back to poc/.env for GEMINI_API_KEY if not set
if _poc_env_path.exists():
    load_dotenv(_poc_env_path, override=False)

# ─────────────────────────────────────────────────────────────────────────────
# Vault secret resolver
# Import here so Vault env vars (VAULT_ENABLED, VAULT_ADDR …) are already
# loaded from .env before the vault module reads them.
# ─────────────────────────────────────────────────────────────────────────────

from agents.vault import get_secret  # noqa: E402  (import after load_dotenv)


def _resolve(key: str, env_fallback: str = "") -> str:
    """
    Resolve a config value using the Vault → env-var fallback chain.

    Args:
        key:          The secret key name (identical in Vault and as an env var).
        env_fallback: Hardcoded default used only when both Vault and env are unset.

    Returns:
        Resolved string value (never None).
    """
    vault_value = get_secret(key)
    if vault_value:
        return vault_value
    return os.getenv(key, env_fallback)


# ─────────────────────────────────────────────────────────────────────────────
# Application Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Application / environment (non-secret, safe to read from env only)
ENV: str = os.getenv("ENV", "development")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
IS_DEVELOPMENT: bool = ENV == "development"

# AI/LLM
GEMINI_API_KEY: str = _resolve("GEMINI_API_KEY") or _resolve("GOOGLE_API_KEY")

# Database (PostgreSQL + pgvector)
DATABASE_URL: str = _resolve(
    "DATABASE_URL",
    "postgresql://ghost:ghostpassword@localhost:5433/ghost_poc"
)

# Message Broker (Redis)
REDIS_URL: str = _resolve("REDIS_URL", "redis://localhost:6380/0")

# Security — all sensitive values resolved via Vault first
JWT_SECRET: str = _resolve("JWT_SECRET", "ghost-agent-super-secret-key-12345678")
JWT_ALGORITHM: str = "HS256"
JWT_TOKEN_EXPIRY_MINUTES: int = int(_resolve("JWT_TOKEN_EXPIRY_MINUTES", "60"))
SLACK_SIGNING_SECRET: str = _resolve("SLACK_SIGNING_SECRET", "")
GITHUB_WEBHOOK_SECRET: str = _resolve("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TOKEN: str = _resolve("GITHUB_TOKEN", "")
PII_SALT: str = _resolve("PII_SALT", "ghost-pii-default-salt-value-987654321")

# ─────────────────────────────────────────────────────────────────────────────
# Vault Configuration (non-secret — controls Vault client behaviour)
# ─────────────────────────────────────────────────────────────────────────────

VAULT_ENABLED: bool = os.getenv("VAULT_ENABLED", "false").lower() == "true"
VAULT_ADDR: str = os.getenv("VAULT_ADDR", "http://localhost:8200")
VAULT_AUTH_METHOD: str = os.getenv("VAULT_AUTH_METHOD", "token")  # token | kubernetes
VAULT_PATH: str = os.getenv("VAULT_PATH", f"ghost/{ENV}")
VAULT_MOUNT: str = os.getenv("VAULT_MOUNT", "secret")
VAULT_K8S_ROLE: str = os.getenv("VAULT_K8S_ROLE", "ghost-agent")
VAULT_CACHE_TTL_SECONDS: int = int(os.getenv("VAULT_CACHE_TTL_SECONDS", "300"))

# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)

# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_config():
    """Validate critical configuration at startup."""
    logger = logging.getLogger(__name__)
    issues = []
    if not GEMINI_API_KEY:
        issues.append("GEMINI_API_KEY is not set — AI agents will not function")
    if not DATABASE_URL:
        issues.append("DATABASE_URL is not set")
    if not REDIS_URL:
        issues.append("REDIS_URL is not set — Celery workers will not start")
    if not SLACK_SIGNING_SECRET and not IS_DEVELOPMENT:
        issues.append("SLACK_SIGNING_SECRET is not set — Slack webhooks will be insecure")

    if VAULT_ENABLED:
        logger.info(
            f"[Config] Vault integration ACTIVE — "
            f"addr={VAULT_ADDR} method={VAULT_AUTH_METHOD} "
            f"path={VAULT_MOUNT}/data/{VAULT_PATH} "
            f"cache_ttl={VAULT_CACHE_TTL_SECONDS}s"
        )
    else:
        logger.info(
            "[Config] Vault integration DISABLED — "
            "secrets resolved from environment variables / .env"
        )

    for issue in issues:
        logger.warning(f"Config warning: {issue}")

    return len(issues) == 0

