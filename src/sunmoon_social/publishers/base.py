"""Publisher base classes."""

from __future__ import annotations

import os


class Publisher:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def env(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"missing secret {name}")
        return value

    def caption(self, post: dict) -> str:
        copy = post["copy"]
        if isinstance(copy, dict):  # a brief that was never fanned out per platform
            copy = copy.get("instagram") or copy.get("facebook") or ""
        return f"{copy}\n\n{post.get('hashtags', '')}".strip()

    def publish(self, post: dict) -> dict:
        raise NotImplementedError


class DryRunPublisher(Publisher):
    """Used for every post unless --live and SOCIAL_LIVE=true."""

    def publish(self, post: dict) -> dict:
        preview = self.caption(post)
        print(f"  [dry-run:{post['platform']}] {preview[:140]}{'…' if len(preview) > 140 else ''}")
        return {"preview": preview}
