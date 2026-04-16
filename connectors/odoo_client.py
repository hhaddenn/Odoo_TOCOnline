"""
Cliente base para Odoo via JSON-RPC (execute_kw).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sync_engine.metrics import increment_sync_total, timed_operation
from sync_engine.retry import RetryPolicy, should_retry_http_status, sleep_with_backoff

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = int(os.getenv("ODOO_TIMEOUT", "30"))
_DEFAULT_RETRIES = int(os.getenv("ODOO_MAX_RETRIES", "3"))


class OdooError(Exception):
    pass


class OdooClient:
    """
    Thin wrapper over Odoo JSON-RPC.

    Usage::

        client = OdooClient(
            base_url="https://odoo.example.com",
            db="mydb",
            username="admin",
            password="admin",
        )
        client.authenticate()
        records = client.execute_kw("res.partner", "search_read", [[]], {"fields": ["name"]})
    """

    def __init__(
        self,
        base_url: str,
        db: str,
        username: str,
        password: str,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_policy = RetryPolicy(max_retries=max_retries)
        self._uid: int | None = None
        self._http = httpx.Client(timeout=timeout)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rpc(self, endpoint: str, method: str, params: dict) -> Any:
        url = f"{self.base_url}/{endpoint}"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": params,
        }
        for attempt in range(1, self.retry_policy.max_retries + 1):
            try:
                with timed_operation(entity="external_api", endpoint=f"odoo:{endpoint}"):
                    resp = self._http.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise OdooError(data["error"])
                increment_sync_total("external_api", f"odoo:{endpoint}", "success")
                return data.get("result")
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = should_retry_http_status(status)
                logger.warning(
                    "Odoo RPC attempt %d/%d status=%s retryable=%s",
                    attempt,
                    self.retry_policy.max_retries,
                    status,
                    retryable,
                )
                if (not retryable) or attempt == self.retry_policy.max_retries:
                    increment_sync_total("external_api", f"odoo:{endpoint}", "error")
                    raise
                sleep_with_backoff(attempt, policy=self.retry_policy)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning(
                    "Odoo RPC transport attempt %d/%d failed: %s",
                    attempt,
                    self.retry_policy.max_retries,
                    exc,
                )
                if attempt == self.retry_policy.max_retries:
                    increment_sync_total("external_api", f"odoo:{endpoint}", "error")
                    raise
                sleep_with_backoff(attempt, policy=self.retry_policy)
        return None  # unreachable

    def _jsonrpc(self, service: str, method: str, args: list[Any]) -> Any:
        return self._rpc(
            "jsonrpc",
            "call",
            {
                "service": service,
                "method": method,
                "args": args,
            },
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def authenticate(self) -> int:
        """Autentica e guarda o uid. Levanta OdooError se falhar."""
        uid = self._jsonrpc("common", "login", [self.db, self.username, self.password])
        # Odoo returns False on wrong credentials
        if not uid:
            raise OdooError("Authentication failed — check db/username/password.")
        self._uid = uid
        logger.info("Odoo authenticated (uid=%s)", uid)
        return uid

    def execute_kw(
        self,
        model: str,
        method: str,
        args: list,
        kwargs: dict | None = None,
    ) -> Any:
        if self._uid is None:
            self.authenticate()
        return self._jsonrpc(
            "object",
            "execute_kw",
            [self.db, self._uid, self.password, model, method, args, kwargs or {}],
        )

    # ── Convenience helpers ───────────────────────────────────────────────────

    def search_read(
        self,
        model: str,
        domain: list,
        fields: list[str],
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        return self.execute_kw(
            model,
            "search_read",
            [domain],
            {"fields": fields, "offset": offset, "limit": limit},
        )

    def health_check(self) -> dict:
        """Retorna version info do servidor Odoo."""
        return self._rpc("web/webclient/version_info", "call", {})

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def client_from_env() -> OdooClient:
    """Cria OdooClient a partir de variáveis de ambiente."""
    return OdooClient(
        base_url=os.environ["ODOO_BASE_URL"],
        db=os.environ["ODOO_DB"],
        username=os.environ["ODOO_USERNAME"],
        password=os.environ["ODOO_PASSWORD"],
    )
