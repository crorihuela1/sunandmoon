"""
ZoomInfo client wrapper.

The ZoomInfo MCP connector is the canonical source. This module wraps it
in a Python interface so the enrichment pipeline can be re-used in any
runtime (cron, FastAPI, CLI, Cowork session, etc.).

There are two modes:

1. **MCP mode** — when running inside Cowork / Claude, the MCP tools
   (build_list, enrich_company, enrich_contact, find_similar) are
   invoked via the MCP runtime. The pipeline imports this module and
   passes a callable that proxies to the MCP tool.

2. **HTTP mode** — when running standalone (cron, server), call ZoomInfo's
   REST API directly. Requires ZOOMINFO_USERNAME / ZOOMINFO_CLIENT_ID /
   ZOOMINFO_PRIVATE_KEY env vars.

Either way, every call is logged to `enrichment_runs` so cost and
provenance are visible later.
"""

from __future__ import annotations

import os
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Data shapes (lightweight — JSON in, JSON out; the pipeline normalizes)
# ---------------------------------------------------------------------

@dataclass
class BuildListQuery:
    """
    Normalized representation of a segment's ZoomInfo search.
    Translated to the underlying API/MCP shape by `to_payload()`.
    """
    industries: list[str] = field(default_factory=list)
    keywords:   list[str] = field(default_factory=list)
    states:     list[str] = field(default_factory=list)
    metros:     list[str] = field(default_factory=list)
    employee_min: int | None = None
    employee_max: int | None = None
    page_size:  int = 100
    max_results: int = 1_000           # safety cap; per-segment override OK

    def to_payload(self) -> dict[str, Any]:
        # Shape mirrors the ZoomInfo MCP build_list inputs. The MCP layer
        # tolerates camelCase or snake_case; we use snake_case throughout.
        return {
            "filters": {
                "industries": self.industries or None,
                "keywords":   self.keywords   or None,
                "states":     self.states     or None,
                "metros":     self.metros     or None,
                "employee_count_min": self.employee_min,
                "employee_count_max": self.employee_max,
            },
            "page_size":   self.page_size,
            "max_results": self.max_results,
        }


# ---------------------------------------------------------------------
# MCP-mode client (the common case in this codebase)
# ---------------------------------------------------------------------

class ZoomInfoMCPClient:
    """
    Pass in callables that invoke the MCP tools. In Cowork these resolve
    to `mcp__plugin_zoominfo_zoominfo__*` tool calls; in unit tests they
    can be replaced with mocks.
    """

    def __init__(
        self,
        build_list_fn:       Callable[[dict], dict],
        enrich_company_fn:   Callable[[dict], dict],
        enrich_contact_fn:   Callable[[dict], dict],
        find_similar_fn:     Callable[[dict], dict] | None = None,
        recommend_fn:        Callable[[dict], dict] | None = None,
    ):
        self._build_list      = build_list_fn
        self._enrich_company  = enrich_company_fn
        self._enrich_contact  = enrich_contact_fn
        self._find_similar    = find_similar_fn
        self._recommend       = recommend_fn

    # ----- builds -----
    def build_company_list(self, q: BuildListQuery) -> list[dict]:
        payload = q.to_payload()
        log.info("zoominfo.build_list payload=%s", json.dumps(payload)[:240])
        resp = self._build_list(payload)
        return _coerce_list(resp, key="companies")

    # ----- enrichment -----
    def enrich_company(self, *, name: str | None = None, domain: str | None = None,
                       zoominfo_id: str | None = None) -> dict:
        payload = {"name": name, "domain": domain, "zoominfo_id": zoominfo_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._enrich_company(payload) or {}

    def enrich_contact(self, *, full_name: str | None = None, company: str | None = None,
                       email: str | None = None, zoominfo_id: str | None = None) -> dict:
        payload = {"full_name": full_name, "company": company,
                   "email": email, "zoominfo_id": zoominfo_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._enrich_contact(payload) or {}


# ---------------------------------------------------------------------
# HTTP-mode client (used by cron jobs / batch scripts running outside Cowork)
# ---------------------------------------------------------------------

class ZoomInfoHTTPClient:
    """
    Standalone HTTPS client. Auth via JWT — ZoomInfo's standard.
    Token cached in-process; refresh-on-401.
    """

    BASE = "https://api.zoominfo.com"

    def __init__(self, username: str | None = None, client_id: str | None = None,
                 private_key: str | None = None, *, timeout: float = 30.0):
        self.username    = username    or os.environ["ZOOMINFO_USERNAME"]
        self.client_id   = client_id   or os.environ["ZOOMINFO_CLIENT_ID"]
        self.private_key = private_key or os.environ["ZOOMINFO_PRIVATE_KEY"]
        self._token: str | None = None
        self._token_expires_at: float = 0
        self._client = httpx.Client(timeout=timeout)

    # ---- auth ----
    def _auth_header(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_expires_at - 60:
            self._refresh_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _refresh_token(self) -> None:
        # ZoomInfo PKI auth. Adjust to client-credentials flow if your
        # account uses that instead.
        r = self._client.post(
            f"{self.BASE}/authenticate",
            json={
                "username":    self.username,
                "clientId":    self.client_id,
                "privateKey":  self.private_key,
            },
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["jwt"]
        # ZoomInfo tokens are ~1hr. Use returned expiry if provided.
        self._token_expires_at = time.time() + body.get("expires_in", 3600)

    # ---- calls ----
    def build_company_list(self, q: BuildListQuery) -> list[dict]:
        url = f"{self.BASE}/search/company"
        page = 1
        all_results: list[dict] = []
        while len(all_results) < q.max_results:
            r = self._client.post(
                url,
                headers=self._auth_header(),
                json={**q.to_payload(), "page": page},
            )
            if r.status_code == 401:
                self._token = None
                continue
            r.raise_for_status()
            body = r.json()
            batch = body.get("data") or body.get("companies") or []
            if not batch:
                break
            all_results.extend(batch)
            page += 1
            if len(batch) < q.page_size:
                break
        return all_results[: q.max_results]

    def enrich_company(self, *, name=None, domain=None, zoominfo_id=None) -> dict:
        r = self._client.post(
            f"{self.BASE}/enrich/company",
            headers=self._auth_header(),
            json={
                "matchCompanyInput": [
                    {"companyName": name, "companyDomain": domain, "companyId": zoominfo_id}
                ],
            },
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else {}

    def enrich_contact(self, *, full_name=None, company=None, email=None, zoominfo_id=None) -> dict:
        r = self._client.post(
            f"{self.BASE}/enrich/contact",
            headers=self._auth_header(),
            json={
                "matchPersonInput": [
                    {"fullName": full_name, "companyName": company,
                     "emailAddress": email, "personId": zoominfo_id}
                ],
            },
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else {}


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _coerce_list(resp: dict | list | None, *, key: str) -> list[dict]:
    """Different ZoomInfo endpoints return data under different keys.
    Smooth that over."""
    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    for k in (key, "data", "results", "items"):
        if isinstance(resp.get(k), list):
            return resp[k]
    return []
