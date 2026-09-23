#!/usr/bin/env python3
"""
One-off: send Meredith a demo email walking her through the Referral OS,
with 3 real sample personalized emails embedded. CC: corihuela@gmail.com.

Run:
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 send_demo_to_meredith.py
"""
from __future__ import annotations

import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

TO_EMAIL   = "meredithbledsoesf@gmail.com"
CC_EMAILS  = ["corihuela@gmail.com"]
TO_DISPLAY = "Meredith Bledsoe"

SUBJECT = "Quick look at our new referral partner system — would love your feedback"

BODY = """Hi Meredith,

Hope your week's going well. Cristian here — wanted to share something I just built for Sun & Moon at 30A and get your reaction before we start sending it out at scale.

Quick context: I have two adjacent vacation houses on Crystal Court in Seagrove Beach (Blue Moon and Golden Sun, sitting side-by-side, sleeping 16 combined). I've been thinking about how to systematically build referral partnerships with wedding planners, private chefs, photographers, and event vendors — people who can recommend the houses to their clients — instead of leaving it to chance.

So I built a system. Here's what it does:

1) Identifies relevant partners at scale.
   950 targets in the database right now — across local 30A wedding planners, photographers, chefs, bachelorette concierges, PLUS drive-market wedding planners in Atlanta, Birmingham, Nashville, New Orleans, Houston, Dallas, Memphis, Charlotte, Raleigh, and a dozen more cities that historically send destination weddings to 30A.

2) Generates personalized outreach per partner.
   Each email references something specific about them (their cuisine specialty, their location, their portfolio focus, their Google review count) and frames our two-houses-together value differently per segment. A chef gets the "two kitchens + shared yard" pitch. A wedding planner gets the "bride's family one house, groom's family the other" pitch. A drive-market planner gets the "your destination-wedding couples need one address not four" pitch.

3) Tracks everything.
   Every email has an invisible open pixel and tracked links. We know who opens, who clicks through to sunandmoon30a.com, and who replies. Status auto-advances (prospect → contacted → responded → engaged → partner) so we always know where each partner is in the relationship lifecycle. Plus deliverability is properly configured (DKIM, SPF, DMARC) so emails actually land in inboxes, not spam.

Here are three sample emails the system generates — one for each of three different partner types. These are real businesses pulled from our database, with real Google review counts and locations.

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

What I'd love your reaction on, when you have a few minutes:

  • Does the voice/tone feel right? (Casual, neighborly, brief — vs salesy)
  • Is the "two adjacent houses" pitch clear enough?
  • Are we missing any obvious partner segments worth targeting?
  • Anything else that jumps out — good or bad?

Reply whenever — no rush. We're about to start sending these out in a
paced 3-week rollout (Tue/Wed/Thu mornings, ramping from 14 to 200 per
day to build sender reputation properly).

Really appreciate the time.

Thanks,
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

    msg = MIMEText(BODY, "plain", "utf-8")
    msg["From"]       = formataddr((name, user))
    msg["To"]         = formataddr((TO_DISPLAY, TO_EMAIL))
    msg["Cc"]         = ", ".join(CC_EMAILS)
    msg["Subject"]    = SUBJECT
    msg["Reply-To"]   = user
    msg["Message-ID"] = make_msgid(domain=user.split("@", 1)[1])

    # SMTP needs ALL recipients in the envelope (both To and Cc)
    recipients = [TO_EMAIL] + CC_EMAILS

    ctx = ssl.create_default_context()
    print(f"Sending to {TO_EMAIL}  (cc: {', '.join(CC_EMAILS)}) ...")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(user, pw)
        s.sendmail(user, recipients, msg.as_string())
    print(f"✓ sent. Message-ID: {msg['Message-ID']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
