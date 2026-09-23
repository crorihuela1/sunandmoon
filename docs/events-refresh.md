# Events refresh — playbook

The 30A events calendar appears in two places, and they must stay in sync:

| Page | Block | Markers | Holds |
|---|---|---|---|
| `events.html` | badge | `LAST_UPDATED_BADGE_{START,END}` | the "Last refreshed" pill |
| `events.html` | featured | `FEATURED_EVENT_{START,END}` | one hero event |
| `events.html` | list | `EVENT_LIST_{START,END}` | two categories: "Festivals & Big Weekends" (dated, ~6 cards) and "Weekly & Recurring" (undated, ~5 cards) |
| `index.html` | featured | `HOME_FEATURED_EVENT_{START,END}` | the same hero event, shorter copy |
| `index.html` | list | `HOME_EVENT_LIST_{START,END}` | the next **4** dated events, one-line copy |

`index.html` is a condensed mirror of the dated events on `events.html`. It carries
no "Weekly & Recurring" section. Never let the two pages disagree about an event's
dates, name or link.

Edit **only between the markers.** Everything outside them — nav, styles, footer,
the surrounding `<section>` wrappers — is hand-maintained; leave it exactly as is.

## Rules

1. **Drop anything finished.** An event whose last day is before today comes out of
   both pages. This is the single most important step: a stale calendar is worse
   than a thin one.
2. **Featured = the next big dated event**, the one a guest booking this week would
   plan around. When the current featured event has passed, promote the soonest
   remaining one and write it fresh.
3. **Verify before publishing.** For every event kept or added, open its official
   page and confirm the dates, times, ticket status and URL. Event sites move dates
   and sell out. If a link 404s, find the new one or drop the event — never leave a
   dead link. If a source contradicts itself, trust the event's own site.
4. **Keep it local.** Everything within about a 40-minute drive of Seagrove Beach:
   30A proper (Seaside, WaterColor, Rosemary, Alys, Grayton, Seagrove), plus Destin,
   Sandestin/Baytowne and Panama City Beach when the event is worth the drive.
5. **Always close with the drive time** from the property, in the same voice as the
   existing copy: `· 5-min walk or drive`, `· 10-min drive west`, `· 35-min drive west`.
6. **Aim for 6 dated cards** on `events.html` and the **first 4** of them on
   `index.html`, in date order. Fewer is fine if that is genuinely all there is;
   do not invent events or pad with things you could not verify.
7. **Refresh the "Weekly & Recurring" block only when something changes** — a market
   going off-season, a concert series ending. Its cards use words where the dated
   cards use numbers (`Year-Round` / `Sat` / `9–1`, or `Through Oct 30` / `Fri` / `8 PM`).
8. **Copy voice:** unhurried, concrete, specific. Name the thing, say when, say what
   it costs if it costs, end with the drive. No exclamation marks, no "don't miss",
   no invented detail. Match the sentences already on the page.
9. Escape `&` as `&amp;` in HTML text. Use `·` as the separator, `–` for time ranges
   and a plain hyphen for day ranges (`15-18`).
10. Every card link carries `target="_blank" rel="noopener"`.

## Markup

**Badge** (`events.html`) — the date is the day the refresh runs:

```html
<div class="updated-badge"><span class="dot"></span>Last refreshed: September 23, 2026</div>
```

**Featured, `events.html`** — the `section-label` names the window, e.g. `Featured · Late September · 2026`:

```html
<section class="events-section">
  <div class="section-label">Featured · Late September · 2026</div>
  <h2 class="section-title">SHORT EDITORIAL LINE ABOUT THE EVENT</h2>
  <div class="event-featured">
    <div class="event-featured-date">
      <span class="month">Sep</span>
      <span class="day">25-27</span>
      <span class="year">2026</span>
    </div>
    <div class="event-featured-body">
      <div class="event-featured-eyebrow">Free · Seafood · Live Music</div>
      <div class="event-featured-name">Event Name · Venue</div>
      <div class="event-featured-meta">Two or three sentences: what happens each day, times, cost, and the drive.</div>
    </div>
    <div class="event-featured-cta">
      <a href="https://example.com/" target="_blank" rel="noopener">Event Info →</a>
    </div>
  </div>
</section>
```

**Featured, `index.html`** — same event, tighter, and the eyebrow is prefixed `Featured · `:

```html
<div class="event-featured">
  <div class="event-featured-date">
    <span class="month">Sep</span>
    <span class="day">25-27</span>
    <span class="year">2026</span>
  </div>
  <div class="event-featured-body">
    <div class="event-featured-eyebrow">Featured · Free · Seafood · Live Music</div>
    <div class="event-featured-name">Event Name · Venue</div>
    <div class="event-featured-meta">One or two sentences, ending with the drive.</div>
  </div>
  <div class="event-featured-cta">
    <a href="https://example.com/" target="_blank" rel="noopener">Event Info →</a>
  </div>
</div>
```

**Dated card** — identical on both pages except the `event-info-meta`, which is a
full sentence on `events.html` and a single clause on `index.html`:

```html
<a href="https://example.com/" target="_blank" rel="noopener" class="event-card">
  <div class="event-date">
    <span class="month">Oct</span>
    <span class="day">15-18</span>
    <span class="span">2026</span>
  </div>
  <div class="event-info">
    <div class="event-info-name">Event Name · Place</div>
    <div class="event-info-meta">What it is, when, what it costs · drive time</div>
  </div>
</a>
```

On `events.html` the cards sit inside the existing
`<div class="events-cat">` → `<div class="events-grid">` wrappers; on `index.html`
the `HOME_EVENT_LIST` block is a bare `<div class="events-grid">`.

## Checks before committing

Run these from the repo root. All must pass:

```bash
# markers still paired and in order
for m in LAST_UPDATED_BADGE FEATURED_EVENT EVENT_LIST; do
  test "$(grep -c "<!-- ${m}_START -->" events.html)" = 1 || echo "BAD $m start"
  test "$(grep -c "<!-- ${m}_END -->" events.html)" = 1 || echo "BAD $m end"
done
for m in HOME_FEATURED_EVENT HOME_EVENT_LIST; do
  test "$(grep -c "<!-- ${m}_START -->" index.html)" = 1 || echo "BAD $m start"
  test "$(grep -c "<!-- ${m}_END -->" index.html)" = 1 || echo "BAD $m end"
done

# the homepage shows 4 cards, the events page 8-12
grep -c 'class="event-card"' index.html    # expect 4
grep -c 'class="event-card"' events.html   # expect 8-12

# tags balance (python3 is available in the runner)
python3 - <<'PY'
from html.parser import HTMLParser
class P(HTMLParser):
    VOID = {"br","img","meta","link","input","hr","source","path","circle","area","col"}
    def __init__(self): super().__init__(); self.stack=[]; self.bad=[]
    def handle_starttag(self,t,a):
        if t not in self.VOID: self.stack.append(t)
    def handle_endtag(self,t):
        if t in self.VOID: return
        if self.stack and self.stack[-1]==t: self.stack.pop()
        else: self.bad.append((t,list(self.stack[-3:])))
for f in ("index.html","events.html"):
    p=P(); p.feed(open(f).read())
    print(f, "unclosed:", p.stack[:5], "mismatched:", p.bad[:5])
PY

# every link the EVENT blocks point at still resolves
# (scoped to event cards + the featured CTA — do not sweep booking/partner links)
{ grep -h 'class="event-card"' events.html index.html
  grep -h -A2 'class="event-featured-cta"' events.html index.html
} | grep -o 'href="https://[^"]*"' | sed 's/href="//;s/"//' | sort -u |
while read u; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -L --max-time 20 -A 'Mozilla/5.0' "$u")
  case "$c" in 2*|3*|401|403|405) ;; *) echo "DEAD $c $u";; esac
done
```

The tag check must report empty `unclosed` and `mismatched` for both files. A `DEAD`
link must be fixed or its event dropped before committing.

## Commit

Only commit when something actually changed — if every event is still current and
nothing needed correcting, leave the tree clean and say so. Bumping the badge alone
is not a reason to commit.

```
Events refresh: YYYY-MM-DD
```

Push to `main`. Cloudflare rebuilds the `sunandmoonhome` Worker automatically and
the change is live at https://sunandmoon30a.com within about a minute.
