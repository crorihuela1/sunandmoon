"""Single-call HTTP publishers: X, Pinterest, Buffer."""

from __future__ import annotations

import requests

from .base import Publisher


class XPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        from requests_oauthlib import OAuth1  # deferred: only needed once X is active

        auth = OAuth1(
            self.env("X_API_KEY"), self.env("X_API_SECRET"),
            self.env("X_ACCESS_TOKEN"), self.env("X_ACCESS_SECRET"))
        resp = requests.post(
            "https://api.x.com/2/tweets",
            json={"text": self.caption(post)[:280]}, auth=auth, timeout=30)
        resp.raise_for_status()
        return {"id": resp.json().get("data", {}).get("id")}


class PinterestPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        media_url = post.get("media_url")
        if not media_url:
            return {"reason": "held: Pinterest pins require media_url", "held": True}
        resp = requests.post(
            "https://api.pinterest.com/v5/pins",
            headers={"Authorization": f"Bearer {self.env('PINTEREST_ACCESS_TOKEN')}"},
            json={
                "board_id": self.env("PINTEREST_BOARD_ID"),
                "title": post["copy"][:100],
                "description": self.caption(post)[:800],
                "link": "https://sunandmoon30a.com",
                "media_source": {"source_type": "image_url", "url": media_url},
            }, timeout=30)
        resp.raise_for_status()
        return {"id": resp.json().get("id")}


class BufferPublisher(Publisher):
    """Aggregator fast path: one token fans out to every profile connected in Buffer."""

    def publish(self, post: dict) -> dict:
        token = self.env("BUFFER_ACCESS_TOKEN")
        profiles = requests.get(
            "https://api.bufferapp.com/1/profiles.json",
            params={"access_token": token}, timeout=30)
        profiles.raise_for_status()
        ids = [p["id"] for p in profiles.json()]
        resp = requests.post(
            "https://api.bufferapp.com/1/updates/create.json",
            data={"text": self.caption(post), "profile_ids[]": ids, "access_token": token},
            timeout=30)
        resp.raise_for_status()
        return {"id": str(resp.json().get("updates", [{}])[0].get("id", "")), "profiles": len(ids)}
