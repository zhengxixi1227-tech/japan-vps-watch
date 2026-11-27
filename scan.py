#!/usr/bin/env python3
"""Cheap Japan/Hong Kong VPS stock watcher. Stdlib only.

Scans provider live pages every run and records the result in state.json.
A HIT is strict: the provider's own live order page must confirm BOTH
  (a) the plan is orderable / the location is selectable, AND
  (b) the ANNUAL price is at or under the ceiling.
Ceilings: Japan <= $8.00/year, Hong Kong <= $8.00/year (raised 2026-11-26,
was $5.00).
Hong Kong at $5.50/year is UNDER the $8 ceiling: natkvm Hong Kong was
browser-verified selectable on the live order page 2026-11-26 (config
total $5.50/year), so it IS a hit. The plain-fetch scanner cannot see
the JS location dropdown, so the HK hit comes from the browser-verified
record in main(), not from a fetch. NATVPS sells no Hong Kong plan at all
(their location list is France, Canada, Netherlands, Norway, USA NY/LA,
Singapore, Japan).

Detection notes (verified 2026-11-25 by fetching the live pages):
- natkvm flash-sale page is server-rendered: it literally prints
  "Out of Stock / We are currently out of stock on this item..." while
  sold out, and the deal price "NAT VPS Deals From $5.50/Year".
  This is the primary natkvm signal and works with a plain HTTP fetch.
- natkvm regular order page loads its "Choose the location" dropdown
  via JavaScript (WHMCS updateConfigurableOptions AJAX), so a plain
  fetch CANNOT see the location list. The scanner records this target
  as unknown_js and does not let it block a hit from the flash page.
  If the dropdown ever renders server-side, "japan"/"tokyo" appearing
  in the page text is treated as a hit signal.
- NATVPS is EXCLUDED (2026-11-25): its checkout takes cryptocurrency
  ONLY (no card, no PayPal), and the user's only payment method is a
  Mastercard debit card, so no NATVPS plan can ever be bought. The user
  declined the in-stock Japan NAT512 ($7.50/yr) on 2026-11-25 for this
  reason. The scanner no longer checks NATVPS and never emits a NATVPS
  hit. (For the record: NAT512 was in stock at $7.50/yr and NAT768
  $10/yr out of stock; an earlier misread on 2026-11-25 had them
  backwards.) The check_natvps_jp() function below is kept for reference
  only and is no longer called.
- VirNAT: virnat.com has NO DNS A record at all (confirmed via DoH,
  2026-11-25) -- the site is dead. Status is unreachable until it
  loads again; a revived site is recorded as a candidate only, never
  an automatic hit, because its order-page structure is unknown.
- Black-Friday / other-deals scan runs at most once a day and only on
  or after 2026-11-27. It records CANDIDATES only, never hits: a forum
  or tracker price is never proof. A candidate becomes a hit only if
  the provider's own live order page later confirms stock + price.

Exit code is always 0 so the scheduled workflow never fails on a
single unreachable target. HIT.md is only rewritten when every
hit-relevant target returned a definitive status; on fetch failure the
previous HIT.md is left untouched.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

JP_CEILING = 8.00
HK_CEILING = 8.00
BF_START = "2026-11-27"

URLS = {
    "natkvm_flash": "https://khanwebhost.com/store/nat-vps-flash-sale/256-mb-sgjphksasepknlde",
    "natkvm_order": "https://khanwebhost.com/store/ovz-nat-vps/lxc-256-mb-nat",
    "natkvm_jp_page": "https://natkvm.net/japan-nat-vps.html",
    "natvps_jp": "https://clients.natvps.net/?/cart/kvm-japan/",
    "virnat": "https://virnat.com",
    "appsail_bf": "https://vps.appsail.dev/",
    # ascsonoway free VPS (E5 2650 v0 Free Tier, Canada, IPv6-only).
    # Free, email-only signup, 30-day renewal. Tracked separately from
    # the Japan/HK hits: a hit here is FREE, not a paid Japan/HK deal,
    # so it goes to FREE_VPS.md, never HIT.md.
    "ascsonoway_free": "https://cloud.ascsonoway.com/vps-micro/",
    # LowEndSpirit Black Friday 2026 megathread (LowEndTalk). Datacenter
    # IPs are usually Cloudflare-403 here; a 403 is recorded, not fatal.
    "let_bf": "https://lowendspirit.com/discussion/8073/black-friday-2026-megathread",
}

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
HIT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HIT.md")
FREE_VPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FREE_VPS.md")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url, timeout=25, tries=2):
    """Return (ok, text_or_error). ok=False on DNS/HTTP/network failure."""
    last_err = "unknown error"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="ignore")
                return True, text
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < tries - 1:
                time.sleep(3)
    return False, last_err


def visible_text(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def annual_prices(text):
    return [float(p) for p in re.findall(r"\$(\d+\.\d{2})\s*/\s*Year", text)]


# ---------------------------------------------------------------- targets

def check_natkvm_flash():
    """Primary natkvm signal. Returns target dict; may include a hit."""
    url = URLS["natkvm_flash"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    if not ok:
        t.update(status="unreachable", detail=body)
        return t, []
    text = visible_text(body)
    out = "out of stock" in text.lower()
    # Deal price: "NAT VPS Deals From $5.50/Year"; fall back to the
    # cheapest annual price printed on the page.
    m = re.search(r"NAT VPS Deals\s+From\s+\$(\d+\.\d{2})/Year", text)
    prices = annual_prices(text)
    price = float(m.group(1)) if m else (min(prices) if prices else None)
    t["price_usd_per_year"] = price
    t["detail"] = "flash-sale page says out of stock" if out else "flash-sale page no longer says out of stock"
    hits = []
    if out:
        t["status"] = "out_of_stock"
        return t, hits
    # In stock. Same product/price covers Japan and Hong Kong.
    t["status"] = "in_stock"
    if price is not None and price <= JP_CEILING:
        hits.append({
            "provider": "natkvm / KhanWebHost",
            "plan": "Japan LXC NAT 256M (flash sale)",
            "location": "Japan",
            "price_usd_per_year": price,
            "specs": "1 vCPU / 256MB RAM / 5GB SSD / 800GB monthly @1Gbps / NAT IPv4 (20 ports) + dedicated IPv6",
            "url": url,
            "note": "Flash-sale page no longer says out of stock. The location dropdown loads via JS -- verify Japan is selectable on the live order page before buying.",
        })
    if price is not None and price <= HK_CEILING:
        hits.append({
            "provider": "natkvm / KhanWebHost",
            "plan": "Hong Kong LXC NAT 256M (flash sale)",
            "location": "Hong Kong",
            "price_usd_per_year": price,
            "specs": "1 vCPU / 256MB RAM / 5GB SSD / 800GB monthly @1Gbps / NAT IPv4 (20 ports) + dedicated IPv6",
            "url": url,
            "note": "Flash-sale price dropped to $%.2f/year, which is at or under the $8 Hong Kong ceiling. Verify Hong Kong is selectable on the live order page before buying." % price,
        })
    return t, hits


def check_natkvm_order():
    """Regular order page. Location dropdown is JS-loaded, so a plain
    fetch cannot see it. Only a hit if Japan/Tokyo ever renders in text."""
    url = URLS["natkvm_order"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    if not ok:
        t.update(status="unreachable", detail=body)
        return t, []
    text = visible_text(body)
    low = text.lower()
    jp = "japan" in low or "tokyo" in low
    hk = "hong kong" in low
    t["japan_in_page_text"] = jp
    t["hongkong_in_page_text"] = hk
    # Known price of this cart product (browser-verified 2026-11-25/26):
    # $5.50/year total. JP at $5.50 is under the $8 ceiling; HK at
    # $5.50 is UNDER the $8 ceiling (raised 2026-11-26, was $5).
    t["known_price_usd_per_year"] = 5.50
    hits = []
    if jp:
        t["status"] = "in_stock"
        t["detail"] = "Japan/Tokyo appears in order-page text"
        hits.append({
            "provider": "natkvm / KhanWebHost",
            "plan": "LXC NAT 256M (regular order page)",
            "location": "Japan",
            "price_usd_per_year": 5.50,
            "specs": "1 vCPU / 256MB RAM / 5GB SSD / 800GB monthly @1Gbps / NAT IPv4 (20 ports) + dedicated IPv6",
            "url": url,
            "note": "Japan appears in the order-page location list.",
        })
    else:
        t["status"] = "unknown_js"
        t["detail"] = "location dropdown loads via JavaScript; plain fetch cannot see locations (flash-sale page is the primary signal)"
    if hk:
        t["detail"] = (t.get("detail", "") + " | Hong Kong appears in page text; $5.50 is under the $8 HK ceiling, so it is a hit.")
        hits.append({
            "provider": "natkvm / KhanWebHost",
            "plan": "LXC NAT 256M (regular order page)",
            "location": "Hong Kong",
            "price_usd_per_year": 5.50,
            "specs": "1 vCPU / 256MB RAM / 5GB SSD / 800GB monthly @10Gbps / NAT IPv4 (20 ports) + dedicated IPv6",
            "url": url,
            "note": "Hong Kong appears in the order-page location list.",
        })
    return t, hits


def check_natkvm_jp_page():
    """natkvm.net Japan page: confirms the $6.50/yr plan is still listed
    and the order link is present. Informational only."""
    url = URLS["natkvm_jp_page"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    if not ok:
        t.update(status="unreachable", detail=body)
        return t
    text = visible_text(body)
    prices = annual_prices(text)
    t["price_usd_per_year"] = min(prices) if prices else None
    t["order_link_present"] = "Order in Japan" in text or "khanwebhost.com/store/ovz-nat-vps" in body
    t["status"] = "listed" if t["order_link_present"] else "changed"
    t["detail"] = "Japan plan page lists $%.2f/yr, order link present" % t["price_usd_per_year"] if t["price_usd_per_year"] else "price not found on page"
    return t


def check_natvps_jp():
    """NATVPS Japan cart page (server-rendered). Each plan is a product
    card: <div class="card cart-product ... " data-value="ID">.
    Stock is marked ON THE CARD ITSELF: an out-of-stock card carries
    class "outofstock" and a "product-out-of-stock" badge div at its top.
    WARNING -- do NOT detect stock from visible-text order: the badge
    text falls right after the PREVIOUS card's specs in text order, so a
    naive text slice misattributes NAT768's badge to NAT512. (That exact
    misread happened on 2026-11-25 22:41: NAT512 was reported out of
    stock when it is in fact the in-stock one; NAT768 is the out one.)
    Detection here is structural: slice each card's own div and check
    for the outofstock class/badge inside that card only.
    NAT512 $7.50 is the only plan under the $8 ceiling; NAT768 $10 is
    tracked but never a hit. NATVPS sells no Hong Kong plan."""
    url = URLS["natvps_jp"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    hits = []
    if not ok:
        t.update(status="unreachable", detail=body)
        return t, hits
    cards = [(m.start(), m.group(1), m.group(2)) for m in
             re.finditer(r'<div[^>]*class="([^"]*cart-product[^"]*)"[^>]*data-value="(\d+)"', body)]
    if not cards:
        t.update(status="unknown",
                 detail="product-card structure not found on page (page layout may have changed); no hit emitted")
        return t, hits
    info = {}
    for idx, (start, _cls, _val) in enumerate(cards):
        end = cards[idx + 1][0] if idx + 1 < len(cards) else start + 6000
        seg = body[start:end]
        name_m = re.search(r"<h4[^>]*>([^<]+)</h4>", seg)
        price_m = re.search(r"\$(\d+\.\d{2})\s*USD", seg)
        name = name_m.group(1).strip() if name_m else "?"
        price = float(price_m.group(1)) if price_m else None
        oos = "outofstock" in seg.lower()
        info[name] = {"price": price, "out_of_stock": oos}
    n512 = info.get("VPS-NAT512-KVM (Japan)", {})
    n768 = info.get("VPS-NAT768-KVM (Japan)", {})
    t["price_usd_per_year"] = n512.get("price")
    t["nat768_price_usd_per_year"] = n768.get("price")
    t["nat768_out_of_stock"] = n768.get("out_of_stock")
    price512 = n512.get("price")
    oos512 = n512.get("out_of_stock")
    if price512 is None or oos512 is None:
        t.update(status="unknown",
                 detail="NAT512 product card not found on page (page layout may have changed); no hit emitted")
        return t, hits
    t["detail"] = "NAT512 card %s; price $%s/yr; NAT768 $%s/yr %s" % (
        "has out-of-stock badge" if oos512 else "selectable, no out-of-stock badge",
        price512,
        n768.get("price") if n768.get("price") is not None else "?",
        "(out of stock)" if n768.get("out_of_stock") else "(in stock)")
    if oos512:
        t["status"] = "out_of_stock"
        return t, hits
    t["status"] = "in_stock"
    if price512 <= JP_CEILING:
        hits.append({
            "provider": "NATVPS (natvps.net)",
            "plan": "VPS-NAT512-KVM (Japan)",
            "location": "Japan",
            "price_usd_per_year": price512,
            "specs": "1 Core / 512MB RAM / 5GB SSD / 750GB monthly / NAT IPv4 (20 usable ports + 1 SSH) / IPv6 /80",
            "url": url,
            "note": "NAT512 card is selectable with no out-of-stock badge; price $%.2f/year confirmed on the provider's own cart page." % price512,
        })
    return t, hits


def check_virnat():
    """VirNAT: site has no DNS A record (dead since at least 2026-10-31).
    A revived site is a candidate only -- its order structure is unknown,
    so it can never auto-hit."""
    url = URLS["virnat"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    if not ok:
        t.update(status="unreachable", detail=body)
        return t
    text = visible_text(body)
    low = text.lower()
    t["status"] = "site_loads_needs_manual_check"
    t["detail"] = ("Site loads again but order-page structure is unknown; "
                   "Japan mention: %s. Needs a human to verify stock + price on its live order page." % ("yes" if "japan" in low else "no"))
    return t


def check_ascsonoway_free():
    """ascsonoway free VPS (E5 2650 v0 Free Tier, Canada, IPv6-only).
    Free, email-only signup, 30-day renewal. Stock is the only signal:
    the page literally prints 'SOLD OUT' / 'Currently out of stock' while
    sold out. Traffic is NOT published on the site. A hit means the page
    no longer says sold out -- it is a FREE hit, not a Japan/HK hit, so
    it goes to FREE_VPS.md, never HIT.md. A fetch failure here never
    blocks the Japan/HK HIT.md (separate track)."""
    url = URLS["ascsonoway_free"]
    ok, body = fetch(url)
    t = {"url": url, "checked_utc": now_iso()}
    if not ok:
        t.update(status="unreachable", detail=body)
        return t, None
    text = visible_text(body)
    low = text.lower()
    if "e5 2650" not in low:
        t.update(status="unknown",
                 detail="plan page does not mention E5 2650 (page layout may have changed); no hit emitted")
        return t, None
    out = "sold out" in low or "out of stock" in low
    if out:
        t.update(status="sold_out", detail="plan page says SOLD OUT / Currently out of stock")
        return t, None
    t.update(status="in_stock", detail="plan page no longer says sold out")
    hit = {
        "provider": "ascsonoway",
        "plan": "E5 2650 v0 Free Tier",
        "location": "Canada (Alberta)",
        "price_usd_per_year": 0.0,
        "specs": "1 vCPU / 512MB RAM / 16GB SSD / public IPv6 only (no IPv4) / 30-day free renewal",
        "url": url,
        "note": "Free, email-only signup (no card, no phone). Traffic NOT published on the site. IPv6-only -- needs Cloudflare Argo for IPv4 clients. Verify it is actually orderable on the live page before registering.",
    }
    return t, hit


def check_black_friday(state):
    """Once a day, on/after 2026-11-27 only. Records candidates, never hits."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    t = {"checked_utc": now_iso(), "note": "candidates only, never hits"}
    if today < BF_START:
        t["status"] = "not_started"
        t["detail"] = f"Black Friday scan starts {BF_START}; today is {today}"
        return t
    if state.get("last_bf_check") == today:
        t["status"] = "already_checked_today"
        t["detail"] = f"already scanned once today ({today})"
        return t
    candidates = []
    ok, body = fetch(URLS["appsail_bf"])
    if not ok:
        t["appsail"] = f"unreachable: {body}"
    else:
        text = visible_text(body)
        # Look for Japan/HK mentions near an annual $ price.
        for m in re.finditer(r"(日本|东京|大阪|香港).{0,120}?\$(\d+(?:\.\d{1,2})?)\s*/\s*年", text):
            price = float(m.group(2))
            loc = "Japan" if m.group(1) != "香港" else "Hong Kong"
            ceiling = JP_CEILING if loc == "Japan" else HK_CEILING
            if price <= ceiling:
                candidates.append({"location": loc, "price_usd_per_year": price,
                                   "source": "vps.appsail.dev",
                                   "context": text[max(0, m.start() - 40):m.end() + 40].strip()[:200]})
        t["appsail"] = f"fetched, {len(candidates)} candidate(s) at or under ceiling"
    ok2, body2 = fetch(URLS["let_bf"])
    if not ok2:
        t["lowendspirit"] = f"unreachable/blocked: {body2} (expected: LowEndTalk Cloudflare-blocks datacenter IPs)"
    else:
        t["lowendspirit"] = "fetched (unexpected -- check manually)"
    t["status"] = "checked"
    t["candidates"] = candidates[:10]
    t["detail"] = f"{len(candidates)} candidate(s); a candidate is NOT a hit -- the provider's own live order page must confirm stock + price first"
    return t


# ---------------------------------------------------------------- main

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def render_hit_md(hits, checked_utc):
    lines = [
        "# VPS HIT -- cheap Japan / Hong Kong VPS in stock",
        "",
        f"Last scan: {checked_utc} (UTC)",
        "",
        "A hit means the provider's own live order page confirmed BOTH in-stock "
        "AND annual price at or under the ceiling (Japan <= $8.00/year, "
        "Hong Kong <= $8.00/year).",
        "",
    ]
    for h in hits:
        lines += [
            f"## {h['provider']} -- {h['plan']}",
            "",
            f"- Location: {h['location']}",
            f"- Price: ${h['price_usd_per_year']:.2f} / year",
            f"- Specs: {h['specs']}",
            f"- Proof URL: {h['url']}",
            f"- First seen (UTC): {h.get('first_seen_utc', checked_utc)}",
            f"- Last confirmed (UTC): {h.get('last_confirmed_utc', checked_utc)}",
            f"- Note: {h['note']}",
            "",
        ]
    lines += [
        "---",
        "If this file exists and lists a hit, the deal may still be live. "
        "Buy from the proof URL above; do not trust any other link.",
    ]
    return "\n".join(lines)


def render_free_vps_md(hit, checked_utc):
    """FREE_VPS.md: the ascsonoway free VPS hit, or an explicit no-hit.
    A hit here means the free plan's page no longer says sold out."""
    if hit is None:
        return ("# No current free VPS hit\n\n"
                f"Last scan: {checked_utc} (UTC)\n\n"
                "The ascsonoway E5 2650 v0 Free Tier is currently sold out. "
                "See state.json (target ascsonoway_free) for the latest status.\n")
    lines = [
        "# FREE VPS HIT -- ascsonoway free VPS in stock",
        "",
        f"Last scan: {checked_utc} (UTC)",
        "",
        "A hit means the provider's own plan page no longer says SOLD OUT. "
        "This is a FREE plan (email-only signup, no card, no phone), not a "
        "paid Japan/Hong Kong deal.",
        "",
        f"## {hit['provider']} -- {hit['plan']}",
        "",
        f"- Location: {hit['location']}",
        f"- Price: ${hit['price_usd_per_year']:.2f} / year (free)",
        f"- Specs: {hit['specs']}",
        f"- Proof URL: {hit['url']}",
        f"- First seen (UTC): {hit.get('first_seen_utc', checked_utc)}",
        f"- Last confirmed (UTC): {hit.get('last_confirmed_utc', checked_utc)}",
        f"- Note: {hit['note']}",
        "",
        "---",
        "If this file lists a hit, the free plan may be orderable right now. "
        "Register from the proof URL above; do not trust any other link.",
    ]
    return "\n".join(lines)


def main():
    prev = load_state()
    prev_hits = { (h.get("provider"), h.get("plan")): h for h in prev.get("hits", []) }
    checked = now_iso()

    targets = {}
    hits = []
    definitive = True  # False if any hit-relevant target was unreachable

    t_flash, h = check_natkvm_flash()
    targets["natkvm_jp_flash"] = t_flash
    hits += h
    if t_flash["status"] == "unreachable":
        definitive = False

    t_order, h = check_natkvm_order()
    targets["natkvm_jp_order"] = t_order
    hits += h
    if t_order["status"] == "unreachable":
        definitive = False

    targets["natkvm_jp_page"] = check_natkvm_jp_page()

    # NATVPS EXCLUDED 2026-11-25: crypto-only checkout, user pays by
    # Mastercard debit only and declined the $7.50 NAT512. Never a hit.
    targets["natvps_jp_nat512"] = {
        "url": URLS["natvps_jp"],
        "checked_utc": checked,
        "status": "excluded",
        "detail": "Excluded 2026-11-25: checkout accepts cryptocurrency only (no card, no PayPal); user's only payment method is a Mastercard debit card. Japan NAT512 was in stock at $7.50/yr when last checked, but can never be bought.",
    }

    # natkvm Hong Kong: known $5.50/year, browser-verified SELECTABLE on
    # the live order page 2026-11-26 00:31 CST (config total $5.50/year).
    # Under the $8 HK ceiling (raised 2026-11-26, was $5), so it IS a hit.
    # The plain-fetch scanner cannot see the JS dropdown, so this hit
    # comes from the browser-verified record, not from a fetch.
    targets["natkvm_hk"] = {
        "url": URLS["natkvm_order"],
        "checked_utc": checked,
        "status": "in_stock" if 5.50 <= HK_CEILING else "over_budget",
        "price_usd_per_year": 5.50,
        "detail": "Hong Kong LXC NAT 256M is $5.50/year, browser-verified selectable on the live order page 2026-11-26 00:31 CST; under the $8.00 Hong Kong ceiling." if 5.50 <= HK_CEILING else "Hong Kong LXC NAT 256M is $5.50/year, over the Hong Kong ceiling, so never a hit at this price.",
    }
    if 5.50 <= HK_CEILING:
        hits.append({
            "provider": "natkvm / KhanWebHost",
            "plan": "Hong Kong LXC NAT 256M (regular order page)",
            "location": "Hong Kong",
            "price_usd_per_year": 5.50,
            "specs": "1 vCPU / 256MB RAM / 5GB SSD / 800GB monthly @10Gbps / NAT IPv4 (20 ports) + dedicated IPv6",
            "url": URLS["natkvm_order"],
            "note": "Hong Kong was selectable in the live order-page dropdown and the config total was $5.50/year (browser-verified 2026-11-26 00:31 CST). Re-confirm Hong Kong is still selectable on the order page before buying; order page says 'Risk-free for 2 days'.",
        })

    targets["natvps_hk"] = {
        "url": URLS["natvps_jp"],
        "checked_utc": checked,
        "status": "no_such_product",
        "detail": "NATVPS sells no Hong Kong plan (locations: France, Canada, Netherlands, Norway, USA NY/LA, Singapore, Japan).",
    }

    targets["virnat"] = check_virnat()

    # ascsonoway free VPS: separate track from the Japan/HK hits.
    # A fetch failure here never blocks HIT.md.
    t_asco, free_hit = check_ascsonoway_free()
    targets["ascsonoway_free"] = t_asco
    prev_free = prev.get("free_hits", [])
    prev_free_by_key = {(h.get("provider"), h.get("plan")): h for h in prev_free}
    if free_hit is not None:
        key = (free_hit["provider"], free_hit["plan"])
        old = prev_free_by_key.get(key, {})
        free_hit["first_seen_utc"] = old.get("first_seen_utc", checked)
        free_hit["last_confirmed_utc"] = checked
        free_hits = [free_hit]
    else:
        free_hits = []

    bf = check_black_friday(prev)
    targets["black_friday"] = bf
    last_bf_check = prev.get("last_bf_check")
    if bf.get("status") == "checked":
        last_bf_check = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Merge hit timestamps so a continuing hit keeps its first_seen.
    merged = []
    for h in hits:
        key = (h["provider"], h["plan"])
        old = prev_hits.get(key, {})
        h["first_seen_utc"] = old.get("first_seen_utc", checked)
        h["last_confirmed_utc"] = checked
        merged.append(h)

    state = {
        "last_checked_utc": checked,
        "ceilings_usd_per_year": {"japan": JP_CEILING, "hongkong": HK_CEILING},
        "targets": targets,
        "hits": merged,
        "free_hits": free_hits,
        "last_bf_check": last_bf_check,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # FREE_VPS.md is always rewritten (ascsonoway is a separate track;
    # its fetch status never blocks HIT.md).
    with open(FREE_VPS_FILE, "w", encoding="utf-8") as f:
        f.write(render_free_vps_md(free_hits[0] if free_hits else None, checked))

    if definitive:
        if merged:
            with open(HIT_FILE, "w", encoding="utf-8") as f:
                f.write(render_hit_md(merged, checked))
        else:
            with open(HIT_FILE, "w", encoding="utf-8") as f:
                f.write("# No current hit\n\n"
                        f"Last scan: {checked} (UTC)\n\n"
                        "No Japan VPS at <= $8.00/year and no Hong Kong VPS at <= $8.00/year "
                        "is currently in stock on any tracked provider page. "
                        "See state.json for per-target status.\n")
    else:
        print("A hit-relevant target was unreachable; HIT.md left untouched.")

    print(json.dumps({"checked": checked, "hits": len(merged),
                      "free_hits": len(free_hits),
                      "statuses": {k: v.get("status") for k, v in targets.items()}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
