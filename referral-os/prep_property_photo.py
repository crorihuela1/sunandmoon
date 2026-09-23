#!/usr/bin/env python3
"""
Take any source photo (the full-resolution shot you have) and produce an
email-optimized version that gets embedded inline in every outreach email.

Output dimensions: 900px wide, JPEG quality 82. Targets roughly 100-200KB —
small enough that 900 emails carrying it is no Gmail-quota concern, large
enough that it looks sharp on Retina screens.

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 prep_property_photo.py path/to/your_original.jpg

Output lands at: ./assets/sun_moon_houses.jpg
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("❌ PIL/Pillow is not installed. Install with:")
    print("   pip3 install --break-system-packages Pillow")
    sys.exit(1)

OUT_PATH    = Path(__file__).parent / "assets" / "sun_moon_houses.jpg"
TARGET_W    = 900
JPEG_QUALITY = 82


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 prep_property_photo.py <source_image>")
        return 1
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"❌ Not found: {src}")
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as img:
        # Convert to RGB if needed (handles PNG with alpha, etc.)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if w > TARGET_W:
            new_h = int(h * TARGET_W / w)
            img = img.resize((TARGET_W, new_h), Image.LANCZOS)
        img.save(OUT_PATH, "JPEG", quality=JPEG_QUALITY, optimize=True)

    size_kb = OUT_PATH.stat().st_size / 1024
    final_w, final_h = Image.open(OUT_PATH).size
    print(f"✓ Wrote {OUT_PATH}")
    print(f"  Dimensions: {final_w} × {final_h}")
    print(f"  Size:       {size_kb:.1f} KB")
    print(f"  Use in:     outreach_email.py / outreach_dispatch.py (automatic — no config change needed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
