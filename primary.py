"""Face value from the primary ticketing platform.

CrowdVolt is a resale order book: it knows what people are asking, not what a
ticket originally cost. Without that, a $91 floor could be a bargain or a
mugging. But every event carries the id it has on whichever platform sold the
tickets first, and DICE -- the largest of them here -- serves its tiers from a
public, unauthenticated endpoint.

That gives two useful numbers:

    original   the cheapest tier ever offered (early-bird face value)
    on_sale    the cheapest tier you could still buy right now, if any

`on_sale` is the one that answers "is this resale listing a good deal", because
it is the alternative you actually have. `original` is the anchor for how far
the event has run up since it went on sale.

Only DICE is implemented. The other platforms CrowdVolt lists (AXS,
Ticketmaster, Eventbrite, RA, POSH...) each need their own integration and
several are openly hostile to automated access; events on those simply carry no
face value, and the dashboard shows a blank rather than a guess.
"""

import json
import re
import urllib.error
import urllib.request

DICE_API = "https://api.dice.fm/events/{}/ticket_types"
DICE_SEARCH = "https://api.dice.fm/unified_search"
UA = "crowdvolt-price-watch/1.0 (personal price monitor)"


def dice_tiers(dice_id, timeout=20, expect_date=None):
    """Every tier DICE lists for an event, cheapest first.

    Prices come back in minor units (cents), hence the /100.

    `expect_date` guards against an id that points somewhere else: one event's
    stored id resolved to a CANCELLED show a month earlier, whose single tier
    was then presented as "what it was going for when it ran out", complete
    with a bargain badge. The date and status arrive in the same response, so
    checking costs nothing.
    """
    if not dice_id:
        return None
    req = urllib.request.Request(DICE_API.format(dice_id),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, TimeoutError):
        return None

    start = ((data.get("dates") or {}).get("event_start_date") or "")[:10]
    if expect_date and start and start != expect_date:
        return None
    if (data.get("status") or "").lower() in ("cancelled", "canceled"):
        return None

    tiers = []
    for t in data.get("ticket_types") or []:
        amount = ((t.get("price") or {}).get("amount"))
        if amount is None:
            continue
        # `increment` is the minimum you are allowed to buy. Group tiers use
        # it -- 19:26 sells "It's a Date: You +1" at $30 with increment 2, so
        # $30 is real but unreachable unless you buy two. Comparing a single
        # resale ticket against it understates the fair price by a third.
        min_qty = ((t.get("limits") or {}).get("increment")) or 1
        tiers.append({"name": t.get("name"),
                      "price": round(amount / 100, 2),
                      "status": t.get("status"),
                      "min_qty": min_qty})
    if not tiers:
        return None
    tiers.sort(key=lambda t: t["price"])

    singles = [t for t in tiers if (t.get("min_qty") or 1) == 1]
    on_sale = [t["price"] for t in singles if t["status"] == "on-sale"]
    return {
        "platform": "DICE",
        "tiers": tiers,
        # same single-ticket rule as the fair value: one event's cheapest tier
        # is $0.00 and another's is a four-person pass, and neither is a price
        # anyone could have paid for one ticket
        "original": min((t["price"] for t in singles), default=None),
        "on_sale": min(on_sale) if on_sale else None,
        "sold_out": not on_sale,
    }


# ---------------------------------------------------------------------------
# matching resale categories to primary tiers
# ---------------------------------------------------------------------------

def _norm(name, strict=False):
    """strict keeps what is inside brackets, loose throws it away.

    Both are needed and in that order: "GA (2-Day Access)" and DICE's
    "GA 2-Day Access" are the same ticket and only match while the bracket
    contents survive -- drop them first and it collapses to plain "GA" and
    silently compares a two-day pass against a one-night one.
    """
    n = (name or "").lower().strip()
    if not strict:
        n = re.sub(r"\(.*?\)|\[.*?\]", " ", n)
    n = n.replace("general admission", "ga")
    n = re.sub(r"\btier\s*\d+\b|\bfinal tier\b|\btier\b", " ", n)
    # "+" distinguishes a product, so it must survive the punctuation strip:
    # otherwise "GA+ Sunday" and "GA Sunday" are the same ticket and the
    # dearer one gets priced at the cheaper one's face value.
    n = n.replace("+", " plus ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return " ".join(n.split())


def _family(name):
    """Which broad product a tier belongs to. Deliberately coarse: the useful
    question is "what is the cheapest comparable ticket I could buy new", and
    promoters name tiers far too freely to match them one to one.

    Strict, because the loose form throws bracket contents away and that is
    exactly where the product marker often lives: "All-In Pass (VIP)" collapses
    to "all in pass", reads as GA, and gets priced against the cheapest
    late-entry tier on the event."""
    n = _norm(name, strict=True)
    if "vip" in n or "backstage" in n or "table" in n or "cabana" in n:
        return "vip"
    return "ga"


def fair_value(face, category, linked_count=None):
    """The cheapest equivalent ticket still buyable on the primary.

    Returns None when nothing comparable is on sale -- which is itself the
    answer "you cannot buy this new any more", handled by the caller.

    `how` records how much to trust it: "exact" when the resale category and a
    primary tier carry the same name, "family" when we only know they are both
    GA (or both VIP). Blond:ish sells a resale category called
    "GA (Before 11PM)" while DICE's matching tier is "Early Entry (Enter Before
    11PM)" -- close enough to compare as GA, not close enough to claim they are
    the same ticket.

    `linked_count` is CrowdVolt's own count of how many promoter ticket types
    it folded into this category, which independently checks the match:
    Crankdat's "General Admission" links four, and DICE lists exactly four GA
    tiers. When the counts disagree we have matched the wrong set -- said
    plainly in `verified` rather than hidden. This is structural, so unlike
    matching on price it cannot be bent by whatever a seller happens to ask.
    """
    tiers = (face or {}).get("tiers") or []
    if not tiers:
        return None

    # Build both candidate pools, then let CrowdVolt's own grouping choose.
    exact = []
    for strict in (True, False):                 # named match beats a loose one
        want = _norm(category, strict)
        exact = [t for t in tiers if _norm(t["name"], strict) == want]
        if exact:
            break
    family = [t for t in tiers if _family(t["name"]) == _family(category)]

    # "event" (every tier) is a last resort, never a candidate the count may
    # choose: for Diplo it is two tiers wide, which happened to match the
    # linked count for Backstage VIP and priced a $467 ticket at $122.
    candidates = [(p, h) for p, h in [(exact, "exact"), (family, "family")] if p]
    if not candidates:
        if not tiers:
            return None
        candidates = [(tiers, "event")]

    if linked_count:
        # linked_count is how many of the promoter's ticket types CrowdVolt
        # folded into this one resale category -- its own judgement about what
        # is interchangeable. 19:26 sells one tier literally named "General
        # Admission" at $42.39, but CrowdVolt groups four GA tiers including
        # one at $30, and a GA resale listing competes with all of them. So
        # the count picks the pool rather than merely grading it: prefer the
        # candidate whose size matches, which is usually the broader one.
        pool, how = min(candidates, key=lambda c: (abs(len(c[0]) - linked_count),
                                                   candidates.index(c)))
    else:
        pool, how = candidates[0]

    # Only tiers you could buy as a single ticket set the fair value; a
    # cheaper group tier is a different product, not a better price.
    singles = [t for t in pool if (t.get("min_qty") or 1) == 1]
    usable = singles or pool          # fall back rather than show nothing
    bundles_only = not singles and bool(pool)

    on_sale = [t for t in usable if t["status"] == "on-sale"]
    sold = [t for t in usable if t["status"] != "on-sale"]
    # Does the choice of pool actually change the answer?
    #
    # Two earlier versions of this were wrong. Comparing pool size against
    # linked_count compared different things and flagged Diplo's plainly
    # correct $122. Comparing every candidate pool then flagged Crankdat's
    # "GA (2-Day Access)", which matches DICE's "GA 2-Day Access" by name --
    # the broader GA family is only a wider net, not a rival reading, so
    # offering $95.42 as an "alternative" was noise.
    #
    # A literal name match settles it. The only real ambiguity is when we
    # OVERRODE that name match -- as for 19:26, where the tier called "General
    # Admission" costs $42.39 but CrowdVolt groups four GA tiers and the
    # cheapest is $30. There a reader could reasonably be shown either, so say
    # so; everywhere else, stay quiet.
    def _cheapest(ts):
        ts = [t for t in ts if (t.get("min_qty") or 1) == 1] or ts
        live = [t["price"] for t in ts if t["status"] == "on-sale"]
        return min(live) if live else max((t["price"] for t in ts), default=None)

    chosen_value = _cheapest(pool)
    exact_value = _cheapest(exact) if exact else None
    ambiguous = bool(exact and how != "exact"
                     and exact_value is not None
                     and chosen_value is not None
                     and exact_value != chosen_value)
    alternatives = sorted({chosen_value, exact_value}) if ambiguous else None

    # A family-only match cannot reach the test above -- there is no exact
    # match to disagree with -- which left it silent exactly where it is least
    # trustworthy. A festival category matching a fourteen-tier pool spanning
    # two nights and three grades is a guess, and should say so.
    if not ambiguous and how == "family" and len(usable) > 1:
        prices = [t["price"] for t in usable]
        if max(prices) >= 2 * min(prices):
            ambiguous = True
            alternatives = [min(prices), max(prices)]

    verified = None if not linked_count else (len(pool) == linked_count)
    on_sale_price = min((t["price"] for t in on_sale), default=None)
    # The last tier to sell out is the dearest one, so its price is what the
    # primary was charging when it ran out.
    last_face = max((t["price"] for t in sold), default=None)

    # A sold-out primary is not "no fair value" -- it is the most interesting
    # case there is. Brunello asks $134 resale against a $61 face; B&L asks
    # exactly face. Anchor on what it last sold for and say so, rather than
    # showing a blank where the answer is most useful.
    return {
        "matched": len(pool),
        "expected": linked_count,
        "verified": verified,
        # the one the UI should surface: true only when picking a different
        # plausible pool would have produced a different price
        "ambiguous": ambiguous,
        "alternatives": alternatives,
        "value": on_sale_price if on_sale_price is not None else last_face,
        "basis": "on-sale" if on_sale_price is not None else
                 ("last-sold" if last_face is not None else None),
        "fair": on_sale_price,
        "sold_out": not on_sale,
        "last_face": last_face,
        "original": min(t["price"] for t in usable),
        # true when every comparable tier forces a multi-ticket purchase
        "bundles_only": bundles_only,
        "how": how,
    }


def dice_find(name, venue, local_date, timeout=20):
    """Recover an event's DICE id when CrowdVolt did not store one.

    Half the events CrowdVolt marks as DICE carry no `dice_event_uqid`, which
    would otherwise leave them without a face value for no better reason than a
    missing field. Search by name and venue, then accept a result ONLY when the
    venue and the local date both agree and exactly one candidate survives --
    a promoter running three Crankdat nights at the same venue is the normal
    case, not the exception, so a near-miss must return nothing rather than the
    wrong night's price.
    """
    if not (name and venue and local_date):
        return None
    body = json.dumps({"q": f"{name} {venue}", "limit": 12}).encode()
    req = urllib.request.Request(
        DICE_SEARCH, data=body, method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, TimeoutError):
        return None

    want_venue = _norm(venue)
    hits = []
    for section in data.get("sections") or []:
        for item in section.get("items") or []:
            if item.get("type") != "event":
                continue
            e = item.get("event") or {}
            venues = [_norm(v.get("name")) for v in e.get("venues") or []]
            start = ((e.get("dates") or {}).get("event_start_date") or "")[:10]
            if want_venue in venues and start == local_date and e.get("id"):
                hits.append(e["id"])
    return hits[0] if len(hits) == 1 else None


def lookup(snap, expect_date=None):
    """Face value for one CrowdVolt event, or None if its platform is not one
    we can read. Adds a per-category fair value alongside the event-wide one."""
    face = dice_tiers(snap.get("dice_id"), expect_date=expect_date)
    if not face:
        return None
    face["id"] = snap.get("dice_id")
    face["by_category"] = {
        row["ticket_type"]: fair_value(face, row["ticket_type"],
                                       row.get("linked_count"))
        for row in snap.get("ticket_types") or []
    }
    return face
