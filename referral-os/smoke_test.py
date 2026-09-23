#!/usr/bin/env python3
"""
Referral OS — smoke test.

Confirms both API keys work end-to-end:
  • Apollo (validates via auth/health endpoint)
  • Google Places (pulls 5 real wedding planners in Seagrove Beach)

Run from the referral-os/ folder:
    python3 smoke_test.py

Uses only Python's standard library — no pip installs needed.
"""
import urllib.request
import urllib.error
import json
import sys


def load_env(path: str = ".env") -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. No quotes / no multiline."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        print(f"❌ Could not find {path}. Are you running this from the referral-os/ folder?")
        sys.exit(1)
    return env


def banner(title: str) -> None:
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def test_apollo(api_key: str) -> bool:
    """Hit Apollo's auth health endpoint to validate the key."""
    banner("APOLLO — validating API key")

    if not api_key:
        print("❌ APOLLO_API_KEY is empty in .env")
        return False

    req = urllib.request.Request(
        "https://api.apollo.io/v1/auth/health",
        headers={
            "X-Api-Key": api_key,
            "Cache-Control": "no-cache",
            # Apollo's Cloudflare WAF blocks Python's default user-agent (error 1010).
            # Identifying ourselves as a normal HTTP client gets us through.
            "User-Agent": "ReferralOS/1.0 (https://github.com/cristian/referral-os)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        if data.get("is_logged_in"):
            print("✅ Apollo API key is live")
            return True
        print(f"⚠ Got response but is_logged_in=false → {data}")
        return False
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode()[:400]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"❌ ERROR: {e}")
        return False


def test_google_places(api_key: str) -> bool:
    """Pull 5 wedding planners near Seagrove Beach as a real-world test."""
    banner("GOOGLE PLACES — wedding planners in Seagrove Beach, FL")

    if not api_key:
        print("❌ GOOGLE_PLACES_API_KEY is empty in .env")
        return False

    body = json.dumps(
        {"textQuery": "wedding planners in Seagrove Beach FL", "pageSize": 5}
    ).encode()
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=body,
        method="POST",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.displayName,places.formattedAddress,"
                "places.websiteUri,places.nationalPhoneNumber,"
                "places.rating,places.userRatingCount"
            ),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode()[:400]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"❌ ERROR: {e}")
        return False

    places = data.get("places", [])
    if not places:
        print("⚠ Key worked, but query returned zero places. Try a broader query.")
        return False

    print(f"✅ Got {len(places)} businesses back:\n")
    for p in places:
        name = p.get("displayName", {}).get("text", "?")
        addr = p.get("formattedAddress", "?")
        site = p.get("websiteUri", "—")
        phone = p.get("nationalPhoneNumber", "—")
        rating = p.get("rating", "—")
        count = p.get("userRatingCount", "—")
        print(f"  • {name}")
        print(f"      {addr}")
        print(f"      web: {site}")
        print(f"      phone: {phone}   ★ {rating} ({count} reviews)")
        print()
    return True


def main() -> int:
    env = load_env()
    apollo_ok = test_apollo(env.get("APOLLO_API_KEY", ""))
    places_ok = test_google_places(env.get("GOOGLE_PLACES_API_KEY", ""))

    banner("RESULTS")
    print(f"Apollo:        {'✅ pass' if apollo_ok else '❌ fail'}")
    print(f"Google Places: {'✅ pass' if places_ok else '❌ fail'}")
    print()
    return 0 if (apollo_ok and places_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
