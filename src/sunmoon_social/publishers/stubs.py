"""Platforms registered but pending implementation/vetting.

Each stub raises with the docs link so an `active` flip without an adapter is
loud, not silent. Implement the adapter as part of the vetting step.
"""

from __future__ import annotations

from .base import Publisher


def _stub(name: str, docs: str) -> type[Publisher]:
    class _Stub(Publisher):
        def publish(self, post: dict) -> dict:
            raise NotImplementedError(
                f"{name} adapter not implemented yet — see {docs}. "
                "Implement in publishers/ as part of the vetting checklist.")

    _Stub.__name__ = f"{name.title().replace('_', '')}Publisher"
    return _Stub


STUB_PUBLISHERS = {
    "tiktok": _stub("tiktok", "https://developers.tiktok.com/doc/content-posting-api-get-started"),
    "youtube": _stub("youtube", "https://developers.google.com/youtube/v3/docs/videos/insert"),
    "google_business_profile": _stub("google_business_profile", "https://developers.google.com/my-business/content/posts-data"),
    "linkedin": _stub("linkedin", "https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api"),
    "nextdoor": _stub("nextdoor", "https://developer.nextdoor.com/docs"),
}
