"""
Ghost Requirement Agent — HashiCorp Vault Secret Client
=======================================================
Centralised secret management using HashiCorp Vault KV v2.

Dev mode  (VAULT_ENABLED=false, the default):
    All calls to get_secret() return None immediately so config.py
    falls back to environment variables / .env exactly as before.
    No Vault dependency is required at runtime.

Prod mode (VAULT_ENABLED=true):
    Secrets are fetched from Vault KV v2 at the path defined by
    VAULT_PATH (default: "secret/ghost/<ENV>").
    Values are cached in-memory for VAULT_CACHE_TTL_SECONDS (default: 300)
    to avoid hammering Vault on every module import.

Authentication methods (controlled by VAULT_AUTH_METHOD):
    "token"      — Static VAULT_TOKEN env var (dev / CI use).
    "kubernetes" — Kubernetes ServiceAccount JWT mounted at
                   /var/run/secrets/kubernetes.io/serviceaccount/token.
                   Uses VAULT_K8S_ROLE to identify the Ghost service account.

Usage (from config.py):
    from agents.vault import get_secret
    JWT_SECRET = get_secret("JWT_SECRET") or os.getenv("JWT_SECRET", "fallback")

Expected Vault KV structure (path: secret/ghost/<env>/):
    GEMINI_API_KEY
    JWT_SECRET
    JWT_TOKEN_EXPIRY_MINUTES
    SLACK_SIGNING_SECRET
    GITHUB_WEBHOOK_SECRET
    GITHUB_TOKEN
    PII_SALT
    DATABASE_URL
    REDIS_URL
"""
import logging
import os
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Vault configuration (read once at import time from environment)
# ─────────────────────────────────────────────────────────────────────────────

_VAULT_ENABLED: bool = os.getenv("VAULT_ENABLED", "false").lower() == "true"
_VAULT_ADDR: str = os.getenv("VAULT_ADDR", "http://localhost:8200")
_VAULT_TOKEN: str = os.getenv("VAULT_TOKEN", "root")          # dev default
_VAULT_AUTH_METHOD: str = os.getenv("VAULT_AUTH_METHOD", "token")  # token | kubernetes
_VAULT_K8S_ROLE: str = os.getenv("VAULT_K8S_ROLE", "ghost-agent")
_VAULT_K8S_MOUNT: str = os.getenv("VAULT_K8S_MOUNT", "kubernetes")
_VAULT_PATH: str = os.getenv("VAULT_PATH", f"ghost/{os.getenv('ENV', 'development')}")
_VAULT_MOUNT: str = os.getenv("VAULT_MOUNT", "secret")         # KV v2 mount
_VAULT_CACHE_TTL: int = int(os.getenv("VAULT_CACHE_TTL_SECONDS", "300"))

# Path where the K8s ServiceAccount JWT is injected by the kubelet
_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"


# ─────────────────────────────────────────────────────────────────────────────
# VaultClient
# ─────────────────────────────────────────────────────────────────────────────

class VaultClient:
    """
    Thread-safe Vault KV v2 client with in-memory secret caching.

    The client lazily authenticates on first use and refreshes the
    cached secret bundle when the TTL expires.
    """

    def __init__(
        self,
        addr: str = _VAULT_ADDR,
        auth_method: str = _VAULT_AUTH_METHOD,
        token: str = _VAULT_TOKEN,
        k8s_role: str = _VAULT_K8S_ROLE,
        k8s_mount: str = _VAULT_K8S_MOUNT,
        path: str = _VAULT_PATH,
        mount: str = _VAULT_MOUNT,
        cache_ttl: int = _VAULT_CACHE_TTL,
    ):
        self._addr = addr
        self._auth_method = auth_method
        self._token = token
        self._k8s_role = k8s_role
        self._k8s_mount = k8s_mount
        self._path = path
        self._mount = mount
        self._cache_ttl = cache_ttl

        self._client = None          # hvac.Client instance (lazy)
        self._cache: dict = {}       # {key: value}
        self._cache_loaded_at: float = 0.0
        self._lock = threading.Lock()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_hvac_client(self):
        """Instantiate and authenticate the hvac client."""
        try:
            import hvac  # type: ignore
        except ImportError:
            raise RuntimeError(
                "hvac is not installed. Add 'hvac>=2.1.0' to requirements.txt "
                "or set VAULT_ENABLED=false to use environment variables."
            )

        client = hvac.Client(url=self._addr)

        if self._auth_method == "kubernetes":
            # Read the pod's ServiceAccount JWT
            try:
                with open(_K8S_SA_TOKEN_PATH) as f:
                    sa_jwt = f.read().strip()
            except FileNotFoundError:
                raise RuntimeError(
                    f"Kubernetes auth selected but ServiceAccount token not found at "
                    f"{_K8S_SA_TOKEN_PATH}. Is this running inside a Kubernetes pod?"
                )
            client.auth.kubernetes.login(
                role=self._k8s_role,
                jwt=sa_jwt,
                mount_point=self._k8s_mount,
            )
            logger.info(
                f"[Vault] Authenticated via Kubernetes auth "
                f"(role={self._k8s_role}, mount={self._k8s_mount})"
            )
        else:
            # Static token auth (dev / CI)
            client.token = self._token
            if not client.is_authenticated():
                raise RuntimeError(
                    f"[Vault] Token authentication failed. "
                    f"Check VAULT_TOKEN and VAULT_ADDR={self._addr}"
                )
            logger.info(f"[Vault] Authenticated via Token auth (addr={self._addr})")

        return client

    def _load_secrets(self) -> dict:
        """
        Fetch the full secret bundle from Vault KV v2.
        Returns a flat dict of {key: value}.
        """
        if self._client is None:
            self._client = self._build_hvac_client()

        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=self._path,
                mount_point=self._mount,
                raise_on_deleted_version=True,
            )
            data: dict = response["data"]["data"]
            logger.info(
                f"[Vault] Loaded {len(data)} secrets from "
                f"{self._mount}/data/{self._path}"
            )
            return data
        except Exception as exc:
            logger.error(
                f"[Vault] Failed to read secrets from "
                f"{self._mount}/data/{self._path}: {exc}"
            )
            raise

    def _ensure_cache(self) -> None:
        """Reload the secret cache if it has expired or was never loaded."""
        now = time.monotonic()
        if now - self._cache_loaded_at > self._cache_ttl:
            logger.debug(
                f"[Vault] Cache expired or cold — refreshing secrets "
                f"(TTL={self._cache_ttl}s)"
            )
            self._cache = self._load_secrets()
            self._cache_loaded_at = now

    # ── Public API ────────────────────────────────────────────────────────────

    def get_secret(self, key: str) -> Optional[str]:
        """
        Return the value of a secret by key.

        Thread-safe: uses a lock to prevent concurrent refreshes.

        Args:
            key: The secret key as stored in Vault (e.g. "JWT_SECRET").

        Returns:
            The secret value string, or None if the key is not present.

        Raises:
            RuntimeError: If Vault authentication or read fails.
        """
        with self._lock:
            self._ensure_cache()
            value = self._cache.get(key)
            if value is None:
                logger.warning(
                    f"[Vault] Key '{key}' not found in "
                    f"{self._mount}/data/{self._path}"
                )
            return value

    def refresh(self) -> None:
        """
        Force an immediate cache refresh on the next get_secret() call.
        Useful after secret rotation.
        """
        with self._lock:
            self._cache_loaded_at = 0.0
            logger.info("[Vault] Cache invalidated — will refresh on next access.")

    def health_check(self) -> dict:
        """
        Return a dict describing Vault connectivity and seal status.
        Safe to call from /api/health/detailed.
        """
        if not _VAULT_ENABLED:
            return {"enabled": False, "status": "disabled"}
        try:
            if self._client is None:
                self._client = self._build_hvac_client()
            status = self._client.sys.read_health_status(method="GET")
            return {
                "enabled": True,
                "status": "ok" if not status.get("sealed") else "sealed",
                "addr": self._addr,
                "path": f"{self._mount}/data/{self._path}",
                "initialized": status.get("initialized", False),
                "sealed": status.get("sealed", False),
            }
        except Exception as exc:
            return {"enabled": True, "status": "error", "detail": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + public helper
# ─────────────────────────────────────────────────────────────────────────────

_vault_client: Optional[VaultClient] = None


def _get_client() -> Optional[VaultClient]:
    """Return the singleton VaultClient, creating it on first call."""
    global _vault_client
    if not _VAULT_ENABLED:
        return None
    if _vault_client is None:
        _vault_client = VaultClient()
    return _vault_client


def get_secret(key: str) -> Optional[str]:
    """
    Primary public interface for secret resolution.

    When VAULT_ENABLED=false (default in dev):
        Returns None immediately — config.py falls back to os.getenv().

    When VAULT_ENABLED=true:
        Returns the value from Vault KV v2, using a TTL-cached bundle.
        Returns None (with a warning) if the key is absent.

    Args:
        key: Secret key name, e.g. "GEMINI_API_KEY".

    Returns:
        Secret value string or None.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        return client.get_secret(key)
    except Exception as exc:
        logger.error(
            f"[Vault] get_secret('{key}') raised an unexpected error: {exc}. "
            f"Falling back to environment variable."
        )
        return None


def get_vault_health() -> dict:
    """Return Vault health for /api/health/detailed endpoint."""
    client = _get_client()
    if client is None:
        return {"enabled": False, "status": "disabled"}
    return client.health_check()
