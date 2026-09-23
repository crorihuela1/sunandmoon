"""
Multi-source enrichment waterfall.

For each company / contact:
  1. ZoomInfo (deepest data, already paid for via MCP)
  2. Apollo  (great backup, $59/mo Basic)
  3. Hunter  (email finder + verifier, free tier 25/mo)
  4. Google Places (local hours, reviews, photos — free 200/mo)
  5. Manual / web search fallback

Each provider sets fields ONLY if they're missing. Providers also write
a row to enrichment_runs so cost + provenance is auditable.

Add new providers by implementing the Provider protocol and appending
to the waterfall list.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .zoominfo_client import ZoomInfoMCPClient, ZoomInfoHTTPClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------

class Provider(Protocol):
    name: str
    cost_per_call_usd: float

    def enrich_company(self, partial: dict) -> dict: ...
    def enrich_contact(self, partial: dict) -> dict: ...


# ---------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------

@dataclass
class ZoomInfoProvider:
    client: ZoomInfoMCPClient | ZoomInfoHTTPClient
    name: str = "zoominfo"
    cost_per_call_usd: float = 0.0   # already paid via seat — track usage anyway

    def enrich_company(self, partial: dict) -> dict:
        r = self.client.enrich_company(
            name=partial.get("name"),
            domain=partial.get("domain"),
            zoominfo_id=partial.get("zoominfo_company_id"),
        )
        return _normalize_zi_company(r) if r else {}

    def enrich_contact(self, partial: dict) -> dict:
        r = self.client.enrich_contact(
            full_name=partial.get("full_name"),
            company=partial.get("company_name"),
            email=partial.get("email"),
            zoominfo_id=partial.get("zoominfo_contact_id"),
        )
        return _normalize_zi_contact(r) if r else {}


@dataclass
class ApolloProvider:
    api_key: str
    name: str = "apollo"
    cost_per_call_usd: float = 0.05   # rough; Basic plan ~$59/mo, ~1200 credits

    def __post_init__(self):
        self._client = httpx.Client(timeout=30.0, headers={
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        })

    def enrich_company(self, partial: dict) -> dict:
        if not (partial.get("domain") or partial.get("name")):
            return {}
        r = self._client.post(
            "https://api.apollo.io/v1/organizations/enrich",
            json={
                "domain":      partial.get("domain"),
                "organization_name": partial.get("name"),
            },
        )
        if r.status_code >= 400:
            log.warning("apollo company enrich failed %s: %s", r.status_code, r.text[:200])
            return {}
        return _normalize_apollo_company(r.json().get("organization", {}))

    def enrich_contact(self, partial: dict) -> dict:
        params = {
            "first_name": partial.get("first_name"),
            "last_name":  partial.get("last_name"),
            "domain":     partial.get("company_domain"),
            "email":      partial.get("email"),
        }
        params = {k: v for k, v in params.items() if v}
        if not params:
            return {}
        r = self._client.post("https://api.apollo.io/v1/people/match", json=params)
        if r.status_code >= 400:
            log.warning("apollo contact enrich failed %s: %s", r.status_code, r.text[:200])
            return {}
        return _normalize_apollo_person(r.json().get("person", {}))


@dataclass
class HunterProvider:
    api_key: str
    name: str = "hunter"
    cost_per_call_usd: float = 0.034   # Starter $34/mo / 1000 = $0.034

    def __post_init__(self):
        self._client = httpx.Client(timeout=15.0)

    def enrich_company(self, partial: dict) -> dict:
        # Hunter has minimal company data; we only use it for email patterns.
        return {}

    def enrich_contact(self, partial: dict) -> dict:
        out: dict[str, Any] = {}
        # Step A: find email if we don't have one
        if not partial.get("email") and partial.get("company_domain") \
                and (partial.get("first_name") or partial.get("last_name")):
            r = self._client.get(
                "https://api.hunter.io/v2/email-finder",
                params={
                    "domain":     partial["company_domain"],
                    "first_name": partial.get("first_name"),
                    "last_name":  partial.get("last_name"),
                    "api_key":    self.api_key,
                },
            )
            if r.status_code < 400:
                d = r.json().get("data", {})
                if d.get("email"):
                    out["email"] = d["email"]
                    out["email_confidence"] = d.get("score")
        # Step B: verify email we have
        email = out.get("email") or partial.get("email")
        if email:
            r = self._client.get(
                "https://api.hunter.io/v2/email-verifier",
                params={"email": email, "api_key": self.api_key},
            )
            if r.status_code < 400:
                d = r.json().get("data", {})
                out["email_verified"] = d.get("status") == "valid"
                out["email_verification_source"] = "hunter"
        return out


# ---------------------------------------------------------------------
# Normalizers — coerce vendor payloads into our schema's column names
# ---------------------------------------------------------------------

def _normalize_zi_company(r: dict) -> dict:
    return _drop_none({
        "zoominfo_company_id": r.get("id") or r.get("companyId"),
        "name":                r.get("name") or r.get("companyName"),
        "domain":              r.get("domain") or r.get("website"),
        "description":         r.get("description"),
        "industry":            r.get("industry"),
        "sub_industry":        r.get("subIndustry"),
        "employee_count":      r.get("employeeCount") or r.get("employees"),
        "revenue_estimate_usd": r.get("revenue"),
        "founded_year":        r.get("foundedYear"),
        "address_line1":       (r.get("address") or {}).get("street") if isinstance(r.get("address"), dict) else None,
        "city":                (r.get("address") or {}).get("city")   if isinstance(r.get("address"), dict) else r.get("city"),
        "state":               (r.get("address") or {}).get("state")  if isinstance(r.get("address"), dict) else r.get("state"),
        "postal_code":         (r.get("address") or {}).get("zip")    if isinstance(r.get("address"), dict) else r.get("zip"),
        "main_phone":          r.get("phone"),
        "linkedin_url":        r.get("linkedinUrl"),
        "facebook_url":        r.get("facebookUrl"),
    })


def _normalize_zi_contact(r: dict) -> dict:
    return _drop_none({
        "zoominfo_contact_id": r.get("id") or r.get("personId"),
        "first_name":          r.get("firstName"),
        "last_name":           r.get("lastName"),
        "title":               r.get("jobTitle") or r.get("title"),
        "seniority":           r.get("seniority"),
        "department":          r.get("department"),
        "email":               r.get("email"),
        "direct_phone":        r.get("directPhone"),
        "mobile_phone":        r.get("mobilePhone"),
        "linkedin_url":        r.get("linkedinUrl"),
    })


def _normalize_apollo_company(r: dict) -> dict:
    return _drop_none({
        "apollo_organization_id": r.get("id"),
        "name":                   r.get("name"),
        "domain":                 r.get("primary_domain") or r.get("website_url"),
        "description":            r.get("short_description"),
        "industry":               r.get("industry"),
        "employee_count":         r.get("estimated_num_employees"),
        "founded_year":           r.get("founded_year"),
        "city":                   r.get("city"),
        "state":                  r.get("state"),
        "linkedin_url":           r.get("linkedin_url"),
        "facebook_url":           r.get("facebook_url"),
        "instagram_handle":       _strip_handle(r.get("twitter_url")),  # apollo sometimes labels both
    })


def _normalize_apollo_person(r: dict) -> dict:
    return _drop_none({
        "apollo_person_id":  r.get("id"),
        "first_name":        r.get("first_name"),
        "last_name":         r.get("last_name"),
        "title":             r.get("title"),
        "seniority":         r.get("seniority"),
        "email":             r.get("email"),
        "linkedin_url":      r.get("linkedin_url"),
    })


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "")}


def _strip_handle(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1] or None


# ---------------------------------------------------------------------
# Waterfall orchestrator
# ---------------------------------------------------------------------

class EnrichmentWaterfall:
    """
    Runs providers in order, only filling in fields that are still empty.
    Yields (provider_name, fields_set, error) per attempt — caller writes
    to enrichment_runs.
    """

    def __init__(self, providers: list[Provider]):
        self.providers = providers

    def enrich_company(self, base: dict):
        merged = dict(base)
        for p in self.providers:
            before = _meaningful_keys(merged)
            try:
                update = p.enrich_company(merged)
            except Exception as e:
                yield (p.name, [], str(e))
                continue
            for k, v in update.items():
                if not merged.get(k):
                    merged[k] = v
            after = _meaningful_keys(merged)
            yield (p.name, sorted(after - before), None)
        return merged

    def enrich_contact(self, base: dict):
        merged = dict(base)
        for p in self.providers:
            before = _meaningful_keys(merged)
            try:
                update = p.enrich_contact(merged)
            except Exception as e:
                yield (p.name, [], str(e))
                continue
            for k, v in update.items():
                if not merged.get(k):
                    merged[k] = v
            after = _meaningful_keys(merged)
            yield (p.name, sorted(after - before), None)
        return merged


def _meaningful_keys(d: dict) -> set[str]:
    return {k for k, v in d.items() if v not in (None, "", [], {})}
