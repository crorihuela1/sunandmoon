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
        return f"{post['copy']}\n\n{post.get('hashtags', '')}".strip()

    def publish(self, post: dict) -> dict:
        raise NotImplementedError


class DryRunPublisher(Publisher):
    """Used for every post unless --live and SOCIAL_LIVE=true."""

    def publish(self, post: dict) -> dict:
        preview = self.caption(post)
        print(f"  [dry-run:{post['platform']}] {preview[:140]}{'…' if len(preview) > 140 else ''}")
        return {"preview": preview}
