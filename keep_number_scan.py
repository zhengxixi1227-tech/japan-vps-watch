#!/usr/bin/env python3
"""Keep-number eSIM lead watcher. Stdlib only.

Scans public Telegram channels (t.me/s/ pages, no login needed) for new
cheap foreign-number eSIM leads, plus FxTwitter timelines of deal posters
(best-effort: FxTwitter may block datacenter IPs; failures are recorded,
never fatal).

Wang's filters (from 2026-11-24/25, standing watch):
  - check-in-style FREE number, OR annual keep cost <= ~100 RMB with
    one-time activation <= ~50 RMB. This scanner hunts the cheaper end:
    one-time buyout like the turtle card (~37 RMB) or keep cost
    <= ~10 RMB/year.
  - NO real-name, NO passport. Must bind accounts + receive SMS codes.
  - White-card writable (eSIM QR). Must be activatable from mainland
    China (no local activation).
  - Stable. Turtle card (+372, 37 RMB one-time) is the benchmark; only
    bring leads FREE or clearly cheaper than turtle.

A CANDIDATE is a paper match only: it mentions a SIM/eSIM with a phone
number + SMS, a cheap/free price signal, and no real-name requirement
mentioned. Known-dead leads are excluded. A candidate is NOT proof: it
only wakes Muse, who verifies live before telling Wang.

Exit code is always 0 so the scheduled workflow never fails on a single
unreachable target. KEEP_NUMBER_LEADS.md is only rewritten when a NEW
candidate appears, so the file changing IS the wake signal for the hook.
"""

import html as htmlmod
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "keep_number_state.json")
LEADS_FILE = os.path.join(BASE, "KEEP_NUMBER_LEADS.md")

# Public Telegram channels (t.me/s/ pages are server-rendered, no login).
# OouoO removed 2026-11-28: it is a contact/bot, not a public channel
# (t.me/s/OouoO redirects to a "Contact @OouoO" page with no posts).
TG_CHANNELS = ["simcardshop01", "CarterCarlChannel"]

# FxTwitter removed 2026-11-28: api.fxtwitter.com returns Cloudflare 403
# from datacenter IPs and the user-timeline JSON shape is unverified.
# X leads reach Wang/Muse directly when he shares them; Telegram
# channels are the proven source for new keep-number leads.

# Anchor: a post must be ABOUT keeping a number, not just any eSIM.
# (Data-only travel eSIMs like Wanderly mention eSIM + free but never
# 保号/接码/0月租, so they no longer match.)
ANCHOR_KW = ["保号", "接码", "收验证码", "0月租", "0 月租", "PayGo",
             "paygo", "买断", "长期保留", "永久号", "永久保号"]
# Context: must mention a SIM/eSIM/number.
CONTEXT_KW = ["eSIM", "esim", "SIM卡", "电话卡", "手机号", "号码"]
# Price signals (cheap/free).
PRICE_KW = ["免费", "0元", "0 元", "¥", "￥", "元", "块钱", "买断",
            "$", "€", "£", "年费", "月租", "包年", "0月租"]
# Real-name / ID requirement markers: post fails the filter.
REALNAME_KW = ["实名", "护照", "passport", "KYC", "视频实名", "人脸",
               "身份证", "身份验证", "需实名", "要实名"]
# Known-dead leads, never re-offer (see MEMORY.md / PROACTIVE_PREFERENCES).
DEAD_LEADS = ["roam", "esimactivator", "hahasim", "one nz", "onenz",
              "sosim", "kitesim", "skinny", "giffgaff", "three uk",
              "threeuk", "ultra mobile", "紫卡", "buyukesim", "simbye",
              "德国o2", "o2 prepaid", "telekom", "voxi", "lycamobile",
              "lebara", "esim.vip", "visible", "simyo", "rakuten",
              "redteago", "haha", "weroam", "roam_esim",
              # CMLink UK: verified dead 2026-11-28 -- activation must
              # happen in UK/EU (not mainland China), permanent roaming
              # is suspended by the carrier, PayGo keep-number reports
              # are disputed (30-day cancellation claims on LINUX DO).
              "cmlink", "cmlink uk", "中国移动英国"]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url, timeout=25, tries=2):
    """Return (ok, text_or_error). ok=False on DNS/HTTP/network failure."""
    last_err = "unknown error"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return True, resp.read().decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001 - record and retry
            last_err = str(e)[:120]
    return False, last_err


def clean_text(raw):
    text = re.sub(r"<[^>]+>", " ", raw)
    text = htmlmod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_dead_lead(text):
    low = text.lower()
    return any(d in low for d in DEAD_LEADS)


def has_realname(text):
    return any(k in text for k in REALNAME_KW)


def is_candidate(text):
    """Paper match: anchor + context + price signal + no real-name marker.

    Excludes travel-data eSIM giveaways (free data, no number) and
    monthly-plan posts (a >=5/month plan is not a keep-number lead).
    """
    if len(text) < 20:
        return False, ""
    if is_dead_lead(text):
        return False, "known dead lead"
    # Travel-data giveaway pattern: free data for trips, no number kept.
    if ("目的地" in text or "出国" in text) and "免费领" in text \
            and "保号" not in text:
        return False, "travel-data giveaway"
    anchor = [k for k in ANCHOR_KW if k in text or k.lower() in text.lower()]
    if not anchor:
        return False, "no keep-number anchor"
    ctx = [k for k in CONTEXT_KW if k in text or k.lower() in text.lower()]
    if not ctx:
        return False, "no SIM/number context"
    price = [k for k in PRICE_KW if k in text]
    if not price:
        return False, "no price signal"
    if has_realname(text):
        return False, "mentions real-name/ID"
    # Monthly-plan posts are not keep-number leads unless 0-monthly-fee.
    if "月套餐" in text and "0月租" not in text and "PayGo" not in text \
            and "paygo" not in text:
        return False, "monthly plan post"
    return True, "anchor=%s price=%s" % (",".join(anchor[:3]),
                                          ",".join(price[:3]))


def scan_telegram(channel):
    """Return (status, posts) where posts = list of dicts."""
    ok, body = fetch("https://t.me/s/%s" % channel)
    if not ok:
        return "unreachable", []
    posts = []
    # Parse: split on data-post markers.
    parts = re.split(r'data-post="%s/(\d+)"' % re.escape(channel), body)
    # parts[0] = before first post; then alternating id, chunk, id, chunk...
    for i in range(1, len(parts) - 1, 2):
        pid = parts[i]
        chunk = parts[i + 1]
        tm = re.search(r'datetime="([^"]+)"', chunk)
        date = tm.group(1)[:10] if tm else ""
        tm2 = re.search(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            chunk, re.S)
        if not tm2:
            continue
        text = clean_text(tm2.group(1))
        if len(text) < 20:
            continue
        posts.append({"id": "%s/%s" % (channel, pid), "date": date,
                      "text": text,
                      "url": "https://t.me/%s/%s" % (channel, pid)})
    if not posts:
        return "no_posts_parsed", []
    return "ok", posts


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {"seen_ids": [], "sources": {}, "leads": []}


def render_leads(leads):
    lines = ["# Keep-number eSIM leads (保号卡线索)",
             "",
             "只记录通过纸面筛选的候选：带号码可收短信、便宜或免费、文中未提实名。",
             "候选不等于可用 —— 需 Muse 人工核实（官网/实际注册流程）后才告诉 Wang。",
             "基准：乌龟卡 +372，37 元一次性买断，每年连一次信号免费续期。",
             "只有出现新候选时本文件才会更新；文件变化 = 有新线索。",
             ""]
    if not leads:
        lines.append("暂无候选。")
        lines.append("")
    for lead in leads:
        lines.append("## %s — %s" % (lead["date"] or "undated", lead["id"]))
        lines.append("")
        lines.append("- 来源：%s" % lead["url"])
        lines.append("- 匹配：%s" % lead["reason"])
        if is_dead_lead(lead["text"]):
            # Lead was added before it was verified dead (e.g. CMLink):
            # keep the dead verdict so a later rewrite does not revive it.
            lines.append("- 状态：已核实 ❌ 不可用（见仓库记录/记忆）")
        else:
            lines.append("- 状态：待核实")
        lines.append("- 摘要：%s" % lead["text"][:400])
        lines.append("")
    lines.append("更新时间（UTC）：%s" % now_iso())
    lines.append("")
    return "\n".join(lines)


def main():
    state = load_state()
    seen = set(state.get("seen_ids", []))
    leads = state.get("leads", [])
    sources = state.get("sources", {})
    new_leads = []

    for channel in TG_CHANNELS:
        status, posts = scan_telegram(channel)
        sources["tg:%s" % channel] = {"status": status,
                                       "checked_utc": now_iso(),
                                       "posts_seen": len(posts)}
        for p in posts:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            ok, reason = is_candidate(p["text"])
            if ok:
                lead = dict(p, reason=reason, source="telegram")
                leads.append(lead)
                new_leads.append(lead)

    # Keep state bounded.
    state["seen_ids"] = sorted(seen)[-2000:]
    state["sources"] = sources
    state["leads"] = leads[-50:]
    state["last_checked_utc"] = now_iso()
    state["new_leads_this_run"] = len(new_leads)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    if new_leads:
        with open(LEADS_FILE, "w", encoding="utf-8") as f:
            f.write(render_leads(leads))
        print("NEW LEADS: %d" % len(new_leads))
        for lead in new_leads:
            print(" - %s %s" % (lead["id"], lead["url"]))
    else:
        print("no new leads")
    for name, info in sources.items():
        print("%s: %s (%s posts)" % (name, info["status"],
                                     info["posts_seen"]))


if __name__ == "__main__":
    main()
