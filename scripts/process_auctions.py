import json, urllib.request, statistics, datetime, re, sys

NOW     = datetime.datetime.now(datetime.timezone.utc)
TODAY   = datetime.date.today().isoformat()
CUTOFF  = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
UID_TTL = datetime.timedelta(hours=1)

COLLECTION_RE = re.compile(r"(.+?)\s*\(\d+/\d+\)$")
COLOR_RE      = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)

def strip_colors(text: str) -> str:
    return COLOR_RE.sub("", text)

def is_sold(auction: dict) -> bool:
    try:
        end_time = datetime.datetime.strptime(
            auction["endTime"][:19], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=datetime.timezone.utc)
    except (KeyError, ValueError):
        return False
    if end_time > NOW:
        return False
    return bool(auction.get("highestBidder") or auction.get("bids"))

def count_stars(lore: list[str]) -> int | None:
    for line in lore:
        clean = strip_colors(line)
        if "Zustand:" in clean:
            n = clean.count("✯")
            return n if n > 0 else None
    return None

def matches_manual(item: dict, manual_items: list[dict]) -> str | None:
    for entry in manual_items:
        if item.get("material") != entry["material"]:
            continue
        if strip_colors(item.get("displayName", "")) != entry["displayName"]:
            continue
        lore = [strip_colors(l) for l in item.get("lore", [])]
        if all(any(req in line for line in lore) for req in entry["loreContains"]):
            return entry["key"]
    return None

def extract_item_key(item: dict, manual_items: list[dict]) -> str | None:
    lore = [strip_colors(l) for l in item.get("lore", [])]

    manual_key = matches_manual(item, manual_items)
    if manual_key:
        return manual_key

    stars = count_stars(item.get("lore", []))
    if stars is None:
        return None

    base = None
    for line in lore:
        m = COLLECTION_RE.match(line.strip())
        if m:
            base = m.group(1).strip()
            break

    if base is None:
        base = strip_colors(item.get("displayName", "UNKNOWN"))

    return f"{base} [{stars}✯]"


# --- API laden ---
try:
    with urllib.request.urlopen("https://api.opsucht.net/auctions/active", timeout=15) as r:
        auctions = json.load(r)
except Exception as e:
    print(f"[ERROR] API fetch failed: {e}")
    sys.exit(1)

print(f"[INFO] {len(auctions)} auctions fetched")

with open("manual_items.json") as f:
    manual_items = json.load(f)

try:
    with open("seen_uids.json") as f:
        seen_uids: dict[str, str] = json.load(f)
except FileNotFoundError:
    seen_uids = {}

try:
    with open("history.json") as f:
        history = json.load(f)
except FileNotFoundError:
    history = {}

# --- Alte UIDs bereinigen ---
seen_uids = {
    uid: ts for uid, ts in seen_uids.items()
    if NOW - datetime.datetime.fromisoformat(ts) < UID_TTL
}

# --- Auktionen verarbeiten ---
new_prices: dict[str, list[float]] = {}
skipped_seen = 0
skipped_not_sold = 0
skipped_no_key = 0

for a in auctions:
    uid = a.get("uid")
    if not uid:
        continue
    if uid in seen_uids:
        skipped_seen += 1
        continue
    if not is_sold(a):
        skipped_not_sold += 1
        continue

    bid = a.get("currentBid")
    if not bid or float(bid) <= 0:
        continue

    key = extract_item_key(a["item"], manual_items)
    if key is None:
        skipped_no_key += 1
        continue

    seen_uids[uid] = NOW.isoformat()
    new_prices.setdefault(key, []).append(float(bid))

print(f"[INFO] New prices: {sum(len(v) for v in new_prices.values())} | "
      f"Skipped: seen={skipped_seen}, not_sold={skipped_not_sold}, no_key={skipped_no_key}")

# --- History updaten ---
for key, prices in new_prices.items():
    item_history = history.setdefault(key, {})
    bucket = item_history.get(TODAY, {"avg": 0.0, "min": prices[0], "max": prices[0], "n": 0})

    all_n   = bucket["n"] + len(prices)
    new_avg = (bucket["avg"] * bucket["n"] + sum(prices)) / all_n
    item_history[TODAY] = {
        "avg": round(new_avg),
        "min": min(bucket["min"], min(prices)),
        "max": max(bucket["max"], max(prices)),
        "n":   all_n,
    }
    history[key] = {d: v for d, v in item_history.items() if d >= CUTOFF}

# --- Speichern ---
with open("seen_uids.json", "w") as f:
    json.dump(seen_uids, f, separators=(",", ":"))

with open("history.json", "w") as f:
    json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

# --- prices.json erzeugen ---
output = {"generated": NOW.isoformat(), "items": {}}
for key, days in history.items():
    daily_avgs = [d["avg"] for d in days.values()]
    if not daily_avgs:
        continue
    today_data = days.get(TODAY, {})
    output["items"][key] = {
        "currentAvg":  today_data.get("avg"),
        "avg30d":      round(statistics.mean(daily_avgs)),
        "min30d":      min(d["min"] for d in days.values()),
        "max30d":      max(d["max"] for d in days.values()),
        "daysTracked": len(days),
        "lastSeen":    TODAY,
    }

with open("prices.json", "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"[INFO] prices.json written with {len(output['items'])} items")