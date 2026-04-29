"""
Cliente base para TOConline via OAuth2 + JSON:API.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from sync_engine.metrics import increment_sync_total, timed_operation
from sync_engine.retry import RetryPolicy, should_retry_http_status, sleep_with_backoff

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api11.toconline.pt"


def _normalize_oauth_token_url(url: str | None) -> str | None:
    """Normaliza URL de token para host OAuth (appXX) quando vier em apiXX."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.startswith("api"):
        oauth_host = "app" + parsed.hostname[3:]
        return urlunparse(parsed._replace(netloc=oauth_host))
    return url


class TOConlineError(Exception):
    pass


class TOConlineClient:
    """
    Thin wrapper sobre a API TOConline (JSON:API).

    Suporta OAuth2 Authorization Code + Refresh Token.

    Usage::

        client = TOConlineClient(
            client_id="...",
            client_secret="...",
            refresh_token="...",   # usar refresh token se já tiver
        )
        data = client.get("/api/customers")
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        authorization_code: str | None = None,
        refresh_token: str | None = None,
        access_token: str | None = None,
        access_token_expires_at: float | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        token_url: str | None = None,
        redirect_uri: str | None = None,
        timeout: int = 30,
        oauth_scope: str = "commercial",
        on_token_refresh: callable | None = None,
        fallback_credentials: dict[str, str | None] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorization_code = authorization_code
        self.refresh_token = refresh_token
        self.base_url = base_url.rstrip("/")
        raw_token_url = token_url or os.getenv("TOCONLINE_TOKEN_URL") or f"{self.base_url}/oauth/token"
        self.token_url = _normalize_oauth_token_url(raw_token_url) or raw_token_url
        self.redirect_uri = redirect_uri or os.getenv("TOCONLINE_REDIRECT_URI") or ""
        self.timeout = timeout
        self.oauth_scope = oauth_scope or "commercial"
        self.retry_policy = RetryPolicy(max_retries=int(os.getenv("TOCONLINE_MAX_RETRIES", "3")))
        self._access_token: str | None = access_token or os.getenv("TOCONLINE_TOKEN") or None
        self._access_token_expires_at: float | None = access_token_expires_at
        self._on_token_refresh = on_token_refresh
        self._fallback_credentials = fallback_credentials or {}
        self._fallback_applied = False
        self._http = httpx.Client(timeout=timeout)
        self._current_company_id: int | None = None
        self._default_document_series_ids: dict[str, int] = {}

    def _is_unauthorized_client_error(self, exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        response = exc.response
        if response is None or response.status_code != 401:
            return False
        return "unauthorized_client" in (response.text or "").lower()

    def _apply_fallback_credentials(self) -> bool:
        if self._fallback_applied or not self._fallback_credentials:
            return False

        fallback_client_id = self._fallback_credentials.get("client_id")
        fallback_client_secret = self._fallback_credentials.get("client_secret")
        if not fallback_client_id or not fallback_client_secret:
            return False

        self.client_id = fallback_client_id
        self.client_secret = fallback_client_secret
        self.refresh_token = self._fallback_credentials.get("refresh_token") or self.refresh_token
        self.authorization_code = self._fallback_credentials.get("authorization_code") or self.authorization_code
        self._access_token = self._fallback_credentials.get("access_token") or self._access_token
        self.token_url = (
            _normalize_oauth_token_url(self._fallback_credentials.get("token_url"))
            or self._fallback_credentials.get("token_url")
            or self.token_url
        )
        self._fallback_applied = True
        logger.warning("TOConline OAuth fallback: a usar credenciais do ambiente apos falha com credenciais da BD")
        return True

    def _oauth_headers(self, auth_mode: str = "basic") -> dict:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if auth_mode == "basic":
            # TOConline pode exigir Basic auth (client_id:client_secret em base64)
            basic_raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
            basic = base64.b64encode(basic_raw).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"
        return headers

    def _candidate_token_urls(self) -> list[str]:
        urls: list[str] = []
        if self.token_url:
            # Prefere sempre host OAuth appXX para reduzir 400 HTML no apiXX.
            normalized = _normalize_oauth_token_url(self.token_url)
            if normalized:
                urls.append(normalized)
            urls.append(self.token_url)

            # Compatibilidade: se token_url ainda vier em apiXX, tenta também appXX.
            parsed = urlparse(self.token_url)
            if parsed.hostname and parsed.hostname.startswith("api"):
                oauth_host = "app" + parsed.hostname[3:]
                swapped = urlunparse(parsed._replace(netloc=oauth_host))
                urls.append(swapped)

        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            if u not in seen:
                unique.append(u)
                seen.add(u)
        return unique

    def _post_token(self, payload: dict) -> httpx.Response:
        last_error: httpx.HTTPStatusError | None = None
        last_request_error: httpx.RequestError | None = None
        token_urls = self._candidate_token_urls()
        auth_modes = ("basic", "body")

        for token_url in token_urls:
            for auth_mode in auth_modes:
                req_payload = dict(payload)
                if auth_mode == "body":
                    req_payload["client_id"] = self.client_id
                    req_payload["client_secret"] = self.client_secret

                try:
                    with timed_operation(entity="external_api", endpoint="toconline:/oauth/token"):
                        resp = self._http.post(
                            token_url,
                            data=req_payload,
                            headers=self._oauth_headers(auth_mode=auth_mode),
                            timeout=self.timeout,
                        )
                except httpx.RequestError as exc:
                    logger.warning("TOConline token request error (%s, %s): %s", token_url, auth_mode, exc)
                    last_request_error = exc
                    continue
                if resp.is_success:
                    increment_sync_total("external_api", "toconline:/oauth/token", "success")
                    if token_url != self.token_url:
                        logger.info("TOConline OAuth: token_url alternativo funcionou (%s)", token_url)
                        self.token_url = token_url
                    return resp

                logger.warning(
                    "TOConline token error (%s, %s): %s — %s",
                    token_url,
                    auth_mode,
                    resp.status_code,
                    (resp.text or "")[:300],
                )
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    increment_sync_total("external_api", "toconline:/oauth/token", "error")

        if last_error:
            raise last_error
        if last_request_error:
            raise TOConlineError(f"Falha de rede ao obter/renovar token TOConline: {last_request_error}")
        raise TOConlineError("Falha ao obter/renovar token TOConline")

    def _notify_token_refresh(self) -> None:
        if not self._on_token_refresh:
            return
        try:
            self._on_token_refresh(
                access_token=self._access_token,
                refresh_token=self.refresh_token,
                token_url=self.token_url,
                access_token_expires_at=self._access_token_expires_at,
            )
        except TypeError:
            try:
                # Compatibilidade com callbacks antigos sem expires_at.
                self._on_token_refresh(
                    access_token=self._access_token,
                    refresh_token=self.refresh_token,
                    token_url=self.token_url,
                )
            except TypeError:
                # Compatibilidade com callbacks antigos sem token_url.
                self._on_token_refresh(access_token=self._access_token, refresh_token=self.refresh_token)

    def _set_token_metadata(self, tokens: dict[str, Any]) -> None:
        expires_in = tokens.get("expires_in")
        try:
            expires_in_int = int(expires_in) if expires_in is not None else None
        except (TypeError, ValueError):
            expires_in_int = None

        if expires_in_int and expires_in_int > 0:
            self._access_token_expires_at = time.time() + expires_in_int
        else:
            self._access_token_expires_at = None

    def _token_expiring_soon(self, skew_seconds: int = 120) -> bool:
        if not self._access_token or not self._access_token_expires_at:
            return False
        return (self._access_token_expires_at - time.time()) <= skew_seconds

    # ── OAuth2 ────────────────────────────────────────────────────────────────

    def _fetch_token_with_code(self) -> None:
        if not self.authorization_code:
            raise TOConlineError("authorization_code não definido.")
        payload: dict = {
            "grant_type": "authorization_code",
            "code": self.authorization_code,
            "scope": self.oauth_scope,
        }
        if self.redirect_uri:
            payload["redirect_uri"] = self.redirect_uri
        resp = self._post_token(payload)
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        self._set_token_metadata(tokens)
        self._notify_token_refresh()
        logger.info("TOConline: token obtido via authorization_code.")

    def _refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise TOConlineError("refresh_token não definido.")
        payload: dict = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": self.oauth_scope,
        }
        resp = self._post_token(payload)
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        self._set_token_metadata(tokens)
        self._notify_token_refresh()
        logger.info("TOConline: token renovado via refresh_token.")

    def authenticate(self, force_refresh: bool = False) -> None:
        """Garante access token; usa refresh apenas quando necessário."""

        def _refresh_or_code() -> None:
            """Tenta refresh; em unauthorized_client tenta authorization_code se existir."""
            try:
                self._refresh_access_token()
            except httpx.HTTPStatusError as exc:
                if self._is_unauthorized_client_error(exc) and self._apply_fallback_credentials():
                    try:
                        self._refresh_access_token()
                        return
                    except httpx.HTTPStatusError:
                        if self.authorization_code:
                            self._fetch_token_with_code()
                            return
                        raise
                if self.authorization_code:
                    self._fetch_token_with_code()
                else:
                    raise

        if force_refresh:
            if self.refresh_token:
                _refresh_or_code()
                return
            if not self._access_token:
                self._fetch_token_with_code()
            return

        if self._access_token and not self._token_expiring_soon():
            return

        if self.refresh_token:
            _refresh_or_code()
        else:
            self._fetch_token_with_code()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict:
        if (not self._access_token) or self._token_expiring_soon():
            self.authenticate()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        endpoint_label = f"toconline:{path}"
        for attempt in range(1, self.retry_policy.max_retries + 1):
            with timed_operation(entity="external_api", endpoint=endpoint_label):
                headers = dict(self._headers)
                if "json" in kwargs:
                    headers.pop("Content-Type", None)
                resp = self._http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)

            if resp.status_code == 401:
                logger.warning("TOConline 401 — a renovar token…")
                self.authenticate(force_refresh=True)
                with timed_operation(entity="external_api", endpoint=endpoint_label):
                    headers = dict(self._headers)
                    if "json" in kwargs:
                        headers.pop("Content-Type", None)
                    resp = self._http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)

            if should_retry_http_status(resp.status_code):
                logger.warning("TOConline %s attempt %d/%d", resp.status_code, attempt, self.retry_policy.max_retries)
                if attempt == self.retry_policy.max_retries:
                    increment_sync_total("external_api", endpoint_label, "error")
                    resp.raise_for_status()
                sleep_with_backoff(attempt, policy=self.retry_policy)
                continue

            resp.raise_for_status()
            increment_sync_total("external_api", endpoint_label, "success")
            return resp.json() if resp.content else None
        return None

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, json=payload)

    def patch(self, path: str, payload: dict) -> Any:
        return self._request("PATCH", path, json=payload)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def health_check(self) -> dict:
        """GET simples para verificar conectividade."""
        return self.get("/api/taxes")

    def get_current_company_id(self) -> int | None:
        if self._current_company_id is not None:
            return self._current_company_id

        data = self.get("/api/current_company")
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            try:
                self._current_company_id = int(items[0].get("id"))
            except (TypeError, ValueError, AttributeError):
                self._current_company_id = None
        return self._current_company_id

    def get_default_document_series_id(self, document_type: str) -> int | None:
        if document_type in self._default_document_series_ids:
            return self._default_document_series_ids[document_type]

        data = self.get("/api/commercial_document_series")
        items = data.get("data") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return None

        matching: list[dict[str, Any]] = []
        current_company_id = self.get_current_company_id()
        for item in items:
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes") or {}
            if attributes.get("document_type") != document_type:
                continue
            if current_company_id is not None and attributes.get("company_id") != current_company_id:
                continue
            if not attributes.get("active", True):
                continue
            matching.append(item)

        if not matching:
            return None

        def _series_sort_key(item: dict[str, Any]) -> tuple[int, int]:
            attributes = item.get("attributes") or {}
            try:
                prefix_value = int(str(attributes.get("prefix") or 0).strip() or 0)
            except (TypeError, ValueError):
                prefix_value = 0
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                item_id = 0
            return (prefix_value, item_id)

        selected = max(matching, key=_series_sort_key)
        try:
            series_id = int(selected.get("id"))
        except (TypeError, ValueError):
            series_id = None

        if series_id is not None:
            self._default_document_series_ids[document_type] = series_id
        return series_id

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def client_from_env() -> TOConlineClient:
    """Cria TOConlineClient a partir de variáveis de ambiente."""
    base_url = os.getenv("TOCONLINE_BASE_URL") or _DEFAULT_BASE_URL
    return TOConlineClient(
        client_id=os.environ["TOCONLINE_CLIENT_ID"],
        client_secret=os.environ["TOCONLINE_CLIENT_SECRET"],
        authorization_code=os.getenv("TOCONLINE_AUTHORIZATION_CODE") or None,
        refresh_token=os.getenv("TOCONLINE_REFRESH_TOKEN") or None,
        access_token=os.getenv("TOCONLINE_TOKEN") or None,
        base_url=base_url,
        token_url=_normalize_oauth_token_url(os.getenv("TOCONLINE_TOKEN_URL")) or None,
        redirect_uri=os.getenv("TOCONLINE_REDIRECT_URI") or None,
    )


def client_from_company(company, on_token_refresh=None) -> TOConlineClient:
    """Cria TOConlineClient a partir de credenciais armazenadas em CompanyConnection.
    
    Args:
        company: Instância de Company model
        on_token_refresh: Callback(access_token, refresh_token) para salvar tokens renovados
    """
    from state.models import CompanyConnection
    
    try:
        conn = CompanyConnection.objects.get(
            company=company,
            system=CompanyConnection.SystemType.TOCONLINE,
            is_active=True,
        )
    except CompanyConnection.DoesNotExist:
        logger.warning(f"CompanyConnection não encontrada para {company.name}; usando .env")
        return client_from_env()
    
    creds = conn.credentials or {}
    # Evita drift em produção: por omissão, usa sempre os tokens da BD (por cliente).
    # Override por .env só é aplicado quando:
    # - faltar credencial na BD, ou
    # - TOCONLINE_FORCE_ENV_TOKENS=True (modo intervenção/manual).
    env_access_token = os.getenv("TOCONLINE_TOKEN") or None
    env_refresh_token = os.getenv("TOCONLINE_REFRESH_TOKEN") or None
    env_auth_code = os.getenv("TOCONLINE_AUTHORIZATION_CODE") or None
    force_env_tokens = os.getenv("TOCONLINE_FORCE_ENV_TOKENS", "False") == "True"

    use_env_access = bool(env_access_token and (force_env_tokens or not creds.get("access_token")))
    use_env_refresh = bool(env_refresh_token and (force_env_tokens or not creds.get("refresh_token")))
    use_env_auth_code = bool(env_auth_code and (force_env_tokens or not creds.get("authorization_code")))

    # Se o operador optar por override (ou a BD estiver incompleta), sincroniza .env -> BD.
    creds_changed = False
    if use_env_access and env_access_token != creds.get("access_token"):
        creds["access_token"] = env_access_token
        creds_changed = True
    if use_env_refresh and env_refresh_token != creds.get("refresh_token"):
        creds["refresh_token"] = env_refresh_token
        creds_changed = True
    if use_env_auth_code and env_auth_code != creds.get("authorization_code"):
        creds["authorization_code"] = env_auth_code
        creds_changed = True
    if creds_changed:
        conn.credentials = creds
        conn.save(update_fields=["credentials", "updated_at"])
    base_url = conn.base_url
    oauth_base_url = creds.get("oauth_url") or creds.get("oauth_base_url") or ""
    token_url = creds.get("token_url") or (f"{oauth_base_url.rstrip('/')}/token" if oauth_base_url else None)
    token_url = _normalize_oauth_token_url(token_url or os.getenv("TOCONLINE_TOKEN_URL") or None)
    if token_url and token_url != creds.get("token_url"):
        creds["token_url"] = token_url
        conn.credentials = creds
        conn.save(update_fields=["credentials", "updated_at"])

    access_token_expires_at = None
    raw_expires_at = creds.get("access_token_expires_at")
    if raw_expires_at is not None:
        try:
            access_token_expires_at = float(raw_expires_at)
        except (TypeError, ValueError):
            access_token_expires_at = None
    
    fallback_credentials = None
    if not force_env_tokens:
        env_client_id = os.environ.get("TOCONLINE_CLIENT_ID")
        env_client_secret = os.environ.get("TOCONLINE_CLIENT_SECRET")
        if env_client_id and env_client_secret:
            fallback_credentials = {
                "client_id": env_client_id,
                "client_secret": env_client_secret,
                "authorization_code": env_auth_code,
                "refresh_token": env_refresh_token,
                "access_token": env_access_token,
                "token_url": os.getenv("TOCONLINE_TOKEN_URL") or token_url,
            }

    return TOConlineClient(
        client_id=creds.get("client_id", os.environ.get("TOCONLINE_CLIENT_ID")),
        client_secret=creds.get("client_secret", os.environ.get("TOCONLINE_CLIENT_SECRET")),
        authorization_code=(env_auth_code if use_env_auth_code else creds.get("authorization_code")) or None,
        refresh_token=(env_refresh_token if use_env_refresh else creds.get("refresh_token")) or None,
        access_token=(env_access_token if use_env_access else creds.get("access_token")) or None,
        access_token_expires_at=access_token_expires_at,
        base_url=base_url,
        token_url=token_url,
        redirect_uri=os.getenv("TOCONLINE_REDIRECT_URI") or None,
        on_token_refresh=on_token_refresh,
        fallback_credentials=fallback_credentials,
    )
