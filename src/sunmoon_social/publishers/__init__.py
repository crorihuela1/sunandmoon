"""Per-platform publisher adapters.

Hard rule: `publish_queue` only ever touches platforms whose status is
`active` in config/apis.yaml AND whose secrets are present. Everything else is
reported and skipped, so flipping a status is the single switch that turns a
channel on.
"""

from __future__ import annotations

import os

from ..config import active_platforms, load_apis, missing_secrets
from .base import DryRunPublisher, Publisher
from .meta import FacebookPublisher, InstagramPublisher, ThreadsPublisher
from .simple_http import BufferPublisher, PinterestPublisher, XPublisher
from .stubs import STUB_PUBLISHERS

PUBLISHERS: dict[str, type[Publisher]] = {
    "meta_instagram": InstagramPublisher,
    "meta_facebook": FacebookPublisher,
    "threads": ThreadsPublisher,
    "x_twitter": XPublisher,
    "pinterest": PinterestPublisher,
    "buffer": BufferPublisher,
    **STUB_PUBLISHERS,
}


def publish_queue(queue: dict, live: bool = False) -> list[dict]:
    """Send queued posts to their platforms. Returns a per-post result log."""
    live = live and os.environ.get("SOCIAL_LIVE", "").lower() == "true"
    apis = load_apis()
    active = active_platforms(apis)
    results = []
    for post in queue.get("posts", []):
        platform = post["platform"]
        cfg = active.get(platform)
        if cfg is None:
            results.append({"platform": platform, "status": "skipped", "reason": "API not active"})
            continue
        if post.get("needs_human_media") and live:
            # Evergreen prompts are briefs for a human (photo + final copy),
            # never auto-published verbatim.
            results.append({"platform": platform, "status": "held",
                            "reason": "needs human media/copy — see queue file"})
            continue
        missing = missing_secrets(cfg)
        if missing and live:
            results.append({"platform": platform, "status": "skipped",
                            "reason": f"missing secrets: {', '.join(missing)}"})
            continue
        publisher_cls = PUBLISHERS.get(platform, DryRunPublisher) if live else DryRunPublisher
        try:
            outcome = publisher_cls(cfg).publish(post)
            results.append({"platform": platform, "status": "published" if live else "dry-run", **outcome})
        except Exception as exc:
            results.append({"platform": platform, "status": "error", "reason": str(exc)})
    return results
