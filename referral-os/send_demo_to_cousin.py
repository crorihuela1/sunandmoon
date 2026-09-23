#!/usr/bin/env python3
"""
One-off: send Agustin (cousin) a demo email walking him through the Referral OS,
with 3 real sample personalized emails embedded AND the strategic overview PDF
attached. CC: corihuela@gmail.com.

Run:
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 send_demo_to_cousin.py
"""
from __future__ import annotations

import mimetypes
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

TO_EMAIL   = "agamboam@lansier.com"
CC_EMAILS  = ["corihuela@gmail.com"]
TO_DISPLAY = "Agustin"   # adjust if a different first-name is preferred

PDF_PATH   = Path(__file__).parent / "Sun_Moon_30A_Referral_OS_Overview.pdf"

SUBJECT = "Project I've been building — would love your reaction"

BODY = """Hey hermano,

Hope you're doing well. Wanted to share something Samantha and I have been building over the last few weeks for Sun & Moon at 30A — and get your honest reaction before we go bigger with it.

Quick context: we have two adjacent vacation houses on Crystal Court in Seagrove Beach (Blue Moon + Golden Sun, side by side, sleeping 16 combined). The two-houses-together format is unusual for the area — perfect for wedding parties, bachelorette weekends, multi-gen family trips, corporate retreats. But the people who could refer those groups to us — wedding planners, chefs, photographers, travel agents — mostly don't know we exist.

So I built a system to fix that systematically instead of leaving it to chance. Here's the short version:

1) Identifies relevant partners at scale.
   969 partners across 19 segments — local 30A wedding planners, chefs, photographers, bachelorette concierges — PLUS drive-market wedding planners in Atlanta, Birmingham, Nashville, New Orleans, Houston, Dallas, Memphis, Charlotte and a dozen more cities that historically send destination weddings to 30A.

2) Generates a personalized email per partner.
   Each one references something specific (their cuisine specialty, their portfolio focus, their location) and frames our two-houses-together value differently per segment. A chef gets the "two kitchens + shared yard" pitch. A wedding planner gets the "bride's family one house, groom's the other" pitch. An Atlanta planner gets the "your destination-wedding couples need one address not four" pitch.

3) Tracks everything.
   Open pixels, click tracking on every link, status pipeline (prospect → contacted → responded → engaged → partner). Currently 45 emails sent, 19 real human opens, 7 clicks, 0 replies yet (we're in week 1 of a paced 3-week rollout).

The attached PDF is a one-pager overview of what we're trying to accomplish and where we stand today.

Below are three actual sample emails the system generates — chef, 30A wedding planner, Atlanta drive-market planner. These are real businesses pulled from our database, real Google review counts, real personalization based on what we know about each one.

═══════════════════════════════════════════════════════════════════
SAMPLE 1 — Private chef (30A local)
═══════════════════════════════════════════════════════════════════
TO:      Marrow Private Chefs (Santa Rosa Beach, FL)
SIGNAL:  5★ Google · 684 reviews · 30A's most-reviewed private chef
SUBJECT: two kitchens, one yard

Hi there,

Saw your reviews — you're the most loved private chef on 30A by a wide
margin. Quick reach-out from one Crystal Court neighbor: I run Sun &
Moon at 30A, two adjacent houses in Seagrove Beach (Blue Moon and
Golden Sun, sitting next to each other on the same street).

The thing that's relevant to you: two full kitchens with two ovens,
plus a shared backyard the two houses open onto. Groups of 12–16 stay
together but the chef gets real prep space — stage in one kitchen,
plate from the other, serve on the yard.

If you ever want a property you can confidently send clients to, I'd
love to send over the listings + put you on our welcome vendor sheet
(and refer you to every group we host).

Open to a quick call, or just reply and I'll send the details.
experience@sunandmoon30a.com

— Cristian


═══════════════════════════════════════════════════════════════════
SAMPLE 2 — Wedding planner (30A local)
═══════════════════════════════════════════════════════════════════
TO:      Kiss the Bride Weddings Destin/30A (Destin, FL)
SIGNAL:  5★ Google · 28 reviews · 30A/Destin destination weddings
SUBJECT: sleeping arrangements for the whole wedding party

Hi there,

Cristian here — I run Sun & Moon at 30A, two adjacent vacation houses
in Seagrove Beach (Blue Moon + Golden Sun, sitting next to each other
on Crystal Court). The thing I think your couples will love: the
houses sleep 16 combined — so the bride's family can take one house,
the groom's family the other, and everyone gets privacy AND
togetherness in one booking.

Way easier than coordinating four different Airbnbs for the
rehearsal-dinner-through-Sunday-brunch flow. The yards connect, so
you've got a shared space for the getting-ready morning + after-
rehearsal hangout.

If that fits any of your upcoming bookings, I'd love to be on your
housing options list — and send referrals back to you for couples who
reach out without a planner yet. Reply or hit
experience@sunandmoon30a.com.

— Cristian


═══════════════════════════════════════════════════════════════════
SAMPLE 3 — Wedding planner (Atlanta drive-market feeder)
═══════════════════════════════════════════════════════════════════
TO:      House Of BASH (Alpharetta, GA — Atlanta metro)
SIGNAL:  4.8★ Google · 132 reviews · Atlanta event/wedding planner
SUBJECT: Atlanta brides looking at 30A

Hi there,

Cristian Orihuela here — I own Sun & Moon at 30A, two adjacent
vacation houses on Crystal Court in Seagrove Beach. Reaching out
because Atlanta is one of the most consistent feeder markets for
destination weddings on 30A, and the format we offer is rare enough
that it's worth knowing about for your couples.

Two houses next door to each other, sleeping 16 combined, two full
kitchens, shared backyard. For an Atlanta wedding party flying down
for a long weekend, this means bride's family in one house, groom's
family in the other, central rehearsal-dinner space outside. One
booking, one address, one transport coordination.

If you'd be open to having us on your list of housing options for
couples doing 30A weddings, I'd love to send the listings. We'd also
send referrals back to you for Atlanta-area couples reaching out to
us. experience@sunandmoon30a.com

— Cristian
═══════════════════════════════════════════════════════════════════

What I'd love your reaction on, whenever you have a few minutes:

  • Does the pitch land? Is the "two adjacent houses" angle clear?
  • Voice/tone — does it sound like me, or too marketing-y?
  • Anything missing from the strategy in the PDF?
  • Any obvious partner segments we're not targeting yet?
  • Honest gut check: is this a thing you'd actually engage with if you got it?

No rush at all — reply whenever. Whether the feedback is "looks great" or "I'd change three things," both are useful.

Thanks for the time.

Un abrazo,
Cristian
"""


def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env()
    user = env["GMAIL_USER"]
    pw   = env["GMAIL_APP_PASSWORD"]
    name = env.get("CRISTIAN_NAME", "Cristian Orihuela")

    if not PDF_PATH.exists():
        print(f"❌ PDF not found: {PDF_PATH}")
        print(f"   Run the PDF generator first, or re-create it.")
        return 1

    msg = EmailMessage()
    msg["From"]       = formataddr((name, user))
    msg["To"]         = formataddr((TO_DISPLAY, TO_EMAIL))
    msg["Cc"]         = ", ".join(CC_EMAILS)
    msg["Subject"]    = SUBJECT
    msg["Reply-To"]   = user
    msg["Message-ID"] = make_msgid(domain=user.split("@", 1)[1])
    msg.set_content(BODY)

    # Attach the PDF
    pdf_bytes = PDF_PATH.read_bytes()
    ctype, _enc = mimetypes.guess_type(str(PDF_PATH))
    maintype, subtype = (ctype or "application/pdf").split("/", 1)
    msg.add_attachment(
        pdf_bytes,
        maintype=maintype,
        subtype=subtype,
        filename=PDF_PATH.name,
    )

    recipients = [TO_EMAIL] + CC_EMAILS

    print(f"Sending to {TO_EMAIL}  (cc: {', '.join(CC_EMAILS)})")
    print(f"  attachment: {PDF_PATH.name} ({len(pdf_bytes):,} bytes)")
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.send_message(msg, from_addr=user, to_addrs=recipients)
    print(f"✓ sent. Message-ID: {msg['Message-ID']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
