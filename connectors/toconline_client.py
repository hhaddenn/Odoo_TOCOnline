"""
Cliente base para TOConline via OAuth2 + JSON:API.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api11.toconline.pt"


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
        base_url: str = _DEFAULT_BASE_URL,
        token_url: str | None = None,
        redirect_uri: str | None = None,
        timeout: int = 30,
        oauth_scope: str = "commercial",
        on_token_refresh: callable | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorization_code = authorization_code
        self.refresh_token = refresh_token
        self.base_url = base_url.rstrip("/")
        self.token_url = (token_url or os.getenv("TOCONLINE_TOKEN_URL") or f"{self.base_url}/oauth/token")
        self.redirect_uri = redirect_uri or os.getenv("TOCONLINE_REDIRECT_URI") or ""
        self.timeout = timeout
        self.oauth_scope = oauth_scope or "commercial"
        self._access_token: str | None = access_token or os.getenv("TOCONLINE_TOKEN") or None
        self._on_token_refresh = on_token_refresh
        self._http = httpx.Client(timeout=timeout)

    def _oauth_headers(self) -> dict:
        # TOConline OAuth exige Basic auth (client_id:client_secret em base64)
        basic_raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic = base64.b64encode(basic_raw).decode("ascii")
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Authorization": f"Basic {basic}",
        }

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
        resp = self._http.post(self.token_url, data=payload, headers=self._oauth_headers())
        if not resp.is_success:
            logger.error("TOConline token error: %s — %s", resp.status_code, resp.text)
        resp.raise_for_status()
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        if self._on_token_refresh:
            self._on_token_refresh(access_token=self._access_token, refresh_token=self.refresh_token)
        logger.info("TOConline: token obtido via authorization_code.")

    def _refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise TOConlineError("refresh_token não definido.")
        payload: dict = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": self.oauth_scope,
        }
        resp = self._http.post(self.token_url, data=payload, headers=self._oauth_headers())
        if not resp.is_success:
            logger.error("TOConline token error: %s — %s", resp.status_code, resp.text)
        resp.raise_for_status()
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        if self._on_token_refresh:
            self._on_token_refresh(access_token=self._access_token, refresh_token=self.refresh_token)
        logger.info("TOConline: token renovado via refresh_token.")

    def authenticate(self, force_refresh: bool = False) -> None:
        """Garante access token; usa refresh apenas quando necessário."""
        if force_refresh:
            if self.refresh_token:
                try:
                    self._refresh_access_token()
                except httpx.HTTPStatusError:
                    # Quando refresh_token expira/revoga, tenta fluxo de authorization_code
                    # se estiver disponível para recuperar automaticamente.
                    if self.authorization_code:
                        self._fetch_token_with_code()
                    else:
                        raise
                return
            if not self._access_token:
                self._fetch_token_with_code()
            return

        if self._access_token:
            return

        if self.refresh_token:
            try:
                self._refresh_access_token()
            except httpx.HTTPStatusError:
                if self.authorization_code:
                    self._fetch_token_with_code()
                else:
                    raise
        else:
            self._fetch_token_with_code()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict:
        if not self._access_token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(1, 4):
            resp = self._http.request(method, url, headers=self._headers, **kwargs)
            if resp.status_code == 401:
                # Token expirado — renova e tenta de novo
                logger.warning("TOConline 401 — a renovar token…")
                self.authenticate(force_refresh=True)
                resp = self._http.request(method, url, headers=self._headers, **kwargs)
            if resp.status_code >= 500:
                logger.warning("TOConline %s attempt %d/%d", resp.status_code, attempt, 3)
                if attempt == 3:
                    resp.raise_for_status()
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
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
        token_url=os.getenv("TOCONLINE_TOKEN_URL") or None,
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
    base_url = conn.base_url
    oauth_base_url = creds.get("oauth_url") or creds.get("oauth_base_url") or ""
    token_url = creds.get("token_url") or (f"{oauth_base_url.rstrip('/')}/token" if oauth_base_url else None)
    
    return TOConlineClient(
        client_id=creds.get("client_id", os.environ.get("TOCONLINE_CLIENT_ID")),
        client_secret=creds.get("client_secret", os.environ.get("TOCONLINE_CLIENT_SECRET")),
        authorization_code=creds.get("authorization_code") or os.getenv("TOCONLINE_AUTHORIZATION_CODE") or None,
        refresh_token=creds.get("refresh_token") or os.getenv("TOCONLINE_REFRESH_TOKEN") or None,
        access_token=creds.get("access_token") or os.getenv("TOCONLINE_TOKEN") or None,
        base_url=base_url,
        token_url=token_url or os.getenv("TOCONLINE_TOKEN_URL") or None,
        redirect_uri=os.getenv("TOCONLINE_REDIRECT_URI") or None,
        on_token_refresh=on_token_refresh,
    )
