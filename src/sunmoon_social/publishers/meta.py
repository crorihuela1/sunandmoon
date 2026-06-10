"""Meta family: Instagram, Facebook Page, Threads (one Meta app, shared review)."""

from __future__ import annotations

import requests

from .base import Publisher

GRAPH = "https://graph.facebook.com/v21.0"
THREADS = "https://graph.threads.net/v1.0"


class InstagramPublisher(Publisher):
    """Two-step container flow; image posts need a public media URL."""

    def publish(self, post: dict) -> dict:
        token = self.env("META_ACCESS_TOKEN")
        account = self.env("IG_BUSINESS_ACCOUNT_ID")
        media_url = post.get("media_url")
        if not media_url:
            return {"reason": "held: Instagram requires media_url (add one or route via Canva export)",
                    "held": True}
        container = requests.post(
            f"{GRAPH}/{account}/media",
            data={"image_url": media_url, "caption": self.caption(post), "access_token": token},
            timeout=30)
        container.raise_for_status()
        published = requests.post(
            f"{GRAPH}/{account}/media_publish",
            data={"creation_id": container.json()["id"], "access_token": token},
            timeout=30)
        published.raise_for_status()
        return {"id": published.json().get("id")}


class FacebookPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        token = self.env("META_ACCESS_TOKEN")
        page = self.env("FB_PAGE_ID")
        payload = {"message": self.caption(post), "access_token": token}
        endpoint = f"{GRAPH}/{page}/feed"
        if post.get("media_url"):
            endpoint = f"{GRAPH}/{page}/photos"
            payload["url"] = post["media_url"]
        resp = requests.post(endpoint, data=payload, timeout=30)
        resp.raise_for_status()
        return {"id": resp.json().get("id") or resp.json().get("post_id")}


class ThreadsPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        token = self.env("THREADS_ACCESS_TOKEN")
        user = self.env("THREADS_USER_ID")
        container = requests.post(
            f"{THREADS}/{user}/threads",
            data={"media_type": "TEXT", "text": self.caption(post)[:500], "access_token": token},
            timeout=30)
        container.raise_for_status()
        resp = requests.post(
            f"{THREADS}/{user}/threads_publish",
            data={"creation_id": container.json()["id"], "access_token": token},
            timeout=30)
        resp.raise_for_status()
        return {"id": resp.json().get("id")}
