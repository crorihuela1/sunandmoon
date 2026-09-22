"""Turn the day's brief (open dates, local events, brand voice) into captions.

With ANTHROPIC_API_KEY set, Claude writes each platform's caption from the
structured brief. Without it — or if the call fails — the engine falls back to
the template copy already on the post, so a missing key never blocks a run.
"""

from __future__ import annotations

import json
import os
from datetime import date

MODEL = "claude-opus-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "instagram": {"type": "string"},
                    "facebook": {"type": "string"},
                    "hashtags": {"type": "string"},
                },
                "required": ["id", "instagram", "facebook", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


def _system(brand: dict) -> str:
    b = brand["brand"]
    voice = "\n".join(f"- {v}" for v in b.get("voice", []))
    units = "\n".join(f"- {u['label']} ({u['key']}): {u.get('angle', '')}" for u in b.get("units", []))
    facts = "\n".join(f"- {f}" for f in brand.get("facts", []))
    return f"""You write social captions for {b['name']}, a two-cottage vacation rental on 30A (Florida's Emerald Coast). Website: {b['website']}. Booking: {b['booking_url']}.

Voice:
{voice}

The cottages:
{units}

The "angle" above is mood and tone guidance only — it is NOT an amenity list. Never turn an angle word into a feature claim.

Facts you may use (never invent others — no prices, amenities, or distances not listed here or in the brief):
{facts}

Rules:
- One post = one idea. Lead with the concrete thing (the open dates, the event), not a greeting.
- Instagram: 2–5 short lines, line breaks between thoughts, no hashtags in the body (they go in the hashtags field), end with a call to action that says "link in bio" (Instagram captions can't carry links).
- Facebook: 1–3 sentences, conversational, and include the booking URL as plain text.
- Dates: write like a human ("Fri Oct 24 – Sun Oct 26", "3 nights"). Never say a date is open unless the brief lists it as open.
- Events: mention them only when the brief includes them, with the drive time given. Tie the event to the open dates when both are present. If an event brief has no open_dates, do not imply a cottage is available — invite people to save the date and keep an eye on the calendar.
- Amenities: name only what the facts list states. If you are not certain the property has something, leave it out. Never upgrade an indoor fireplace into an outdoor firepit, a patio into a deck, or a community pool into a private one.
- No emojis except at most one, and only if it earns its place. No exclamation-point stacking. No "don't miss out" clichés.
- hashtags: 6–10 tags, space-separated, always including the brand's fixed tags given in the brief.
- Each brief says what the photo shows ("photo_shows"). Do not describe the image, but do not contradict it either — don't write about the porch over a kitchen photo. You never see the image, so never claim details of it."""


def write_captions(brief: dict, brand: dict) -> dict[str, dict] | None:
    """Return {post_id: {instagram, facebook, hashtags}} or None on any failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  copywriter: ANTHROPIC_API_KEY not set — using template copy")
        return None
    try:
        import anthropic
    except ImportError:
        print("  copywriter: anthropic SDK not installed — using template copy")
        return None

    client = anthropic.Anthropic()
    user = (
        f"Today is {date.today().strftime('%A, %B %-d, %Y')}. Write captions for each post in this brief. "
        f"Return one entry per post id.\n\n{json.dumps(brief, indent=2)}"
    )
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=_system(brand),
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.RateLimitError as exc:
        print(f"  copywriter: rate limited ({exc.message}) — using template copy")
        return None
    except anthropic.APIStatusError as exc:
        print(f"  copywriter: API error {exc.status_code} ({exc.message}) — using template copy")
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  copywriter: connection error ({exc}) — using template copy")
        return None

    if response.stop_reason != "end_turn":
        print(f"  copywriter: stop_reason={response.stop_reason} — using template copy")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print("  copywriter: could not parse response — using template copy")
        return None
    out = {p["id"]: p for p in data.get("posts", [])}
    print(f"  copywriter: wrote {len(out)} caption set(s) with {MODEL} "
          f"({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
    return out
