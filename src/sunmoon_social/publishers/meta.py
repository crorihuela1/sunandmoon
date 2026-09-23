"""Meta family: Instagram, Facebook Page, Threads (one Meta app, shared review)."""

from __future__ import annotations

import requests

from .base import Publisher

GRAPH = "https://graph.facebook.com/v26.0"
THREADS = "https://graph.threads.net/v1.0"


def _post(url: str, data: dict, timeout: int = 30) -> dict:
    """POST to the Graph API, surfacing Meta's own error text on failure.

    requests' raise_for_status() reports only the status code; Meta puts the
    actual reason (and often a user-facing hint) in the JSON body, which is
    the only thing that makes a 400 diagnosable.
    """
    resp = requests.post(url, data=data, timeout=timeout)
    if resp.ok:
        return resp.json()
    try:
        err = resp.json().get("error", {})
    except ValueError:
        raise RuntimeError(f"{resp.status_code} from {url}: {resp.text[:300]}")
    bits = [f"{resp.status_code} {err.get('type', 'error')}"]
    if err.get("code") is not None:
        bits.append(f"code {err['code']}" + (f"/{err['error_subcode']}" if err.get("error_subcode") else ""))
    detail = " ".join(bits) + f": {err.get('message', '(no message)')}"
    if err.get("error_user_title") or err.get("error_user_msg"):
        detail += f" — {err.get('error_user_title', '')} {err.get('error_user_msg', '')}".rstrip()
    raise RuntimeError(detail)


class InstagramPublisher(Publisher):
    """Two-step container flow; image posts need a public media URL."""

    def publish(self, post: dict) -> dict:
        token = self.env("META_ACCESS_TOKEN")
        account = self.env("IG_BUSINESS_ACCOUNT_ID")
        media_url = post.get("media_url")
        if not media_url:
            return {"reason": "held: Instagram requires media_url (add one or route via Canva export)",
                    "held": True}
        data = {"image_url": media_url, "caption": self.caption(post), "access_token": token}
        if post.get("alt_text"):
            data["alt_text"] = post["alt_text"]
        container = _post(f"{GRAPH}/{account}/media", data)
        published = _post(f"{GRAPH}/{account}/media_publish",
                          {"creation_id": container["id"], "access_token": token})
        return {"id": published.get("id")}


class FacebookPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        token = self.env("META_ACCESS_TOKEN")
        page = self.env("FB_PAGE_ID")
        payload = {"message": self.caption(post), "access_token": token}
        endpoint = f"{GRAPH}/{page}/feed"
        if post.get("media_url"):
            # /photos takes the caption as `message` too; `url` must be public.
            endpoint = f"{GRAPH}/{page}/photos"
            payload["url"] = post["media_url"]
        resp = _post(endpoint, payload)
        return {"id": resp.get("id") or resp.get("post_id")}


class ThreadsPublisher(Publisher):
    def publish(self, post: dict) -> dict:
        token = self.env("THREADS_ACCESS_TOKEN")
        user = self.env("THREADS_USER_ID")
        container = _post(f"{THREADS}/{user}/threads",
                          {"media_type": "TEXT", "text": self.caption(post)[:500],
                           "access_token": token})
        resp = _post(f"{THREADS}/{user}/threads_publish",
                     {"creation_id": container["id"], "access_token": token})
        return {"id": resp.get("id")}
