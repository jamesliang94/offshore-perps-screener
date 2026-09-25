"""
Offshore Perps Screener
-----------------------
Lists every coin with a perpetual future on big offshore venues (Binance,
Bybit, OKX, Bitget) doing >= MIN_VOLUME_USD in 24h volume, and shows which
US exchanges list that coin for spot trading.

Perp data: CoinGecko public derivatives endpoint (works from the US).
US listings: each exchange's own public API.

Run:  pip install requests
      python offshore_perps_screener.py
"""

import os
import re
from collections import defaultdict

import requests

MIN_VOLUME_USD = 300_000_000

# Scan any CoinGecko derivatives market whose name contains one of these words
PERP_VENUE_KEYWORDS = ["binance", "bybit", "okx", "bitget"]

# Coins to print a detailed diagnostic for, whether or not they qualify
WATCHLIST = ["ZEC"]

# Optional free CoinGecko "Demo" API key (recommended on GitHub Actions)
CG_KEY = os.environ.get("COINGECKO_API_KEY", "")

TIMEOUT = 30
HEADERS = {"User-Agent": "perps-screener/2.0"}

ALIASES = {"XBT": "BTC", "XDG": "DOGE", "XXBT": "BTC", "XETH": "ETH"}
QUOTES = ("FDUSD", "USDT", "USDC", "BUSD", "USD", "EUR", "GBP", "BTC", "ETH")


DIAG = {}


def norm(sym: str) -> str:
    """Uppercase, strip 1000x/1M multipliers, map exchange-specific aliases."""
    s = (sym or "").upper().strip()
    s = re.sub(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])", "", s)
    return ALIASES.get(s, s)


def get(url, headers=None):
    r = requests.get(url, headers=headers or HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------- US exchange listings (spot) ----------

def coinbase():
    return {norm(p["base_currency"]) for p in get("https://api.exchange.coinbase.com/products")
            if p.get("status") == "online"}


def kraken():
    out = set()
    for pair in get("https://api.kraken.com/0/public/AssetPairs")["result"].values():
        ws = pair.get("wsname")
        if ws and "/" in ws:
            out.add(norm(ws.split("/")[0]))
    return out


def gemini():
    quotes = ("usdt", "usdc", "gusd", "rlusd", "usd", "btc", "eth", "eur", "gbp", "sgd", "dai")
    out = set()
    for s in get("https://api.gemini.com/v1/symbols"):
        s = s.lower()
        if s.endswith("perp"):
            continue
        for q in quotes:
            if s.endswith(q) and len(s) > len(q):
                out.add(norm(s[: -len(q)]))
                break
    return out


def binance_us():
    return {norm(s["baseAsset"]) for s in get("https://api.binance.us/api/v3/exchangeInfo")["symbols"]
            if s.get("status") == "TRADING"}


def cryptocom():
    items = get("https://api.crypto.com/exchange/v1/public/get-instruments").get("result", {}).get("data", [])
    return {norm(i["base_ccy"]) for i in items
            if i.get("inst_type") == "CCY_PAIR" and i.get("base_ccy")}


def bitstamp():
    return {norm(p["name"].split("/")[0]) for p in get("https://www.bitstamp.net/api/v2/trading-pairs-info/")
            if p.get("trading", "Enabled") == "Enabled"}


def uphold():
    return {norm(a["code"]) for a in get("https://api.uphold.com/v0/assets") if a.get("code")}


def coinbase_us_perps():
    """Coins with a US-regulated perpetual-style future on Coinbase Derivatives (CDE)."""
    from datetime import datetime, timezone
    data = get("https://api.coinbase.com/api/v3/brokerage/market/products"
               "?product_type=FUTURE&limit=1000")
    out = set()
    now_year = datetime.now(timezone.utc).year
    products = data.get("products", [])
    DIAG["cb_futures_total"] = len(products)
    for prod in products:
        d = prod.get("future_product_details") or {}
        blob = str(prod).upper()
        for w in WATCHLIST:
            if w in blob:
                DIAG.setdefault("cb_watch", []).append(
                    f"{prod.get('product_id')} | {d.get('display_name') or prod.get('display_name')} | "
                    f"venue={d.get('venue')} root={d.get('contract_root_unit')} expiry={d.get('contract_expiry')}")
        if (d.get("venue") or "").lower() not in ("cde", ""):
            continue
        name = (d.get("display_name") or prod.get("display_name") or "").upper()
        expiry = d.get("contract_expiry") or ""
        # Perp-style contracts are ~5-year dated futures or labeled PERP
        is_perp_style = "PERP" in name or (expiry[:4].isdigit() and int(expiry[:4]) >= now_year + 3)
        if not is_perp_style:
            continue
        root = d.get("contract_root_unit") or prod.get("base_display_symbol") or ""
        if root:
            out.add(norm(root))
    return out


US_EXCHANGES = [  # (name, column label, loader)
    ("Coinbase", "CB", coinbase),
    ("Kraken", "KRK", kraken),
    ("Gemini", "GEM", gemini),
    ("Binance.US", "BUS", binance_us),
    ("Crypto.com", "CDC", cryptocom),
    ("Bitstamp", "BSTP", bitstamp),
    ("Uphold", "UPH", uphold),
]


# ---------- Offshore perps ----------

def base_from(t):
    if t.get("index_id"):
        return norm(t["index_id"])
    sym = (t.get("symbol") or "").upper().replace("-", "").replace("_PERP", "").replace("SWAP", "")
    for q in QUOTES:
        if sym.endswith(q) and len(sym) > len(q):
            return norm(sym[: -len(q)])
    return norm(sym)


def offshore_perps():
    h = dict(HEADERS)
    if CG_KEY:
        h["x-cg-demo-api-key"] = CG_KEY
    coins = defaultdict(dict)  # base -> {venue: best volume}
    venues_seen = set()
    for t in get("https://api.coingecko.com/api/v3/derivatives", h):
        market = t.get("market") or ""
        if not any(k in market.lower() for k in PERP_VENUE_KEYWORDS):
            continue
        if t.get("contract_type") != "perpetual":
            continue
        venue = market.split(" (")[0].replace(" Futures", "")
        venues_seen.add(venue)
        vol = float(t.get("volume_24h") or 0)
        base = base_from(t)
        if base in WATCHLIST or any(w in (t.get("symbol") or "").upper() for w in WATCHLIST):
            DIAG.setdefault("cg_watch", []).append(
                f"{market} | {t.get('symbol')} | index_id={t.get('index_id')} -> {base} | vol ${vol/1e6:,.0f}M")
        if vol < MIN_VOLUME_USD:
            continue
        coins[base][venue] = max(vol, coins[base].get(venue, 0))
    return coins, venues_seen


# ---------- Report ----------

def main():
    us, failed = {}, []
    for name, label, fn in US_EXCHANGES:
        try:
            us[label] = fn()
            print(f"Loaded {len(us[label]):>5} assets from {name}")
        except Exception as e:
            us[label] = None
            failed.append(name)
            print(f"WARNING: couldn't load {name}: {e}")

    try:
        us_perps = coinbase_us_perps()
        print(f"Loaded {len(us_perps):>5} coins with US perp-style futures (Coinbase Derivatives)")
    except Exception as e:
        us_perps = None
        print(f"WARNING: couldn't load Coinbase Derivatives perps: {e}")

    coins, venues_seen = offshore_perps()
    print(f"\nPerp venues scanned: {', '.join(sorted(venues_seen)) or 'none found'}")
    print(f"Threshold: ${MIN_VOLUME_USD/1e6:,.0f}M 24h volume on at least one venue")
    print(f"{len(coins)} coins qualify\n")
    if failed:
        print(f"NOTE: {', '.join(failed)} failed to load ('?' below), so 'not listed' results may be incomplete.\n")

    labels = [lab for _, lab, _ in US_EXCHANGES]
    rows = []
    for base, venues in coins.items():
        flags = {lab: ("?" if us[lab] is None else ("Y" if base in us[lab] else "-")) for lab in labels}
        rows.append({
            "base": base,
            "top_vol": max(venues.values()),
            "venues": ", ".join(f"{v} {vol/1e6:,.0f}" for v, vol in sorted(venues.items(), key=lambda x: -x[1])),
            "flags": flags,
            "us_count": sum(f == "Y" for f in flags.values()),
            "us_perp": "?" if us_perps is None else ("Y" if base in us_perps else "-"),
        })
    rows.sort(key=lambda r: r["top_vol"], reverse=True)

    def table(title, subset):
        print(f"=== {title} ({len(subset)}) ===")
        if not subset:
            print("(none)\n")
            return
        print(f"{'Coin':<10}{'Top vol $M':>11}  " + "".join(f"{l:>5}" for l in labels) + f"{'#US':>5}{'USPERP':>8}   Venues (vol $M)")
        for r in subset:
            print(f"{r['base']:<10}{r['top_vol']/1e6:>11,.0f}  "
                  + "".join(f"{r['flags'][l]:>5}" for l in labels)
                  + f"{r['us_count']:>5}{r['us_perp']:>8}   {r['venues']}")
        print()

    table("NO US perp-style futures (offshore-only perps)", [r for r in rows if r["us_perp"] == "-"])
    table("NOT listed on any US exchange checked (spot)", [r for r in rows if r["us_count"] == 0])
    table("Listed on only 1-2 US exchanges", [r for r in rows if 1 <= r["us_count"] <= 2])
    table("ALL qualifying coins", rows)

    print("Legend: " + ", ".join(f"{lab}={name}" for name, lab, _ in US_EXCHANGES))
    print("\n=== DIAGNOSTICS ===")
    print(f"Coinbase futures products returned: {DIAG.get('cb_futures_total', 'n/a')}")
    for w in WATCHLIST:
        print(f"\n-- {w} --")
        print("Offshore perps seen (any volume):")
        for line in [l for l in DIAG.get("cg_watch", []) if w in l.upper()] or ["  none found in CoinGecko data"]:
            print("  " + line)
        print("Coinbase futures products mentioning it:")
        for line in [l for l in DIAG.get("cb_watch", []) if w in l.upper()] or ["  none found"]:
            print("  " + line)
        print("Spot listings: " + ", ".join(
            f"{lab}={'?' if us[lab] is None else ('Y' if w in us[lab] else '-')}" for lab in labels))
        print(f"Qualifies (>= threshold): {'yes' if w in coins else 'no'} | "
              f"USPERP: {'?' if us_perps is None else ('Y' if w in us_perps else '-')}")
    print()
    print("USPERP = US-regulated perp-style future on Coinbase Derivatives")
    print("Y = listed, - = not listed, ? = exchange data unavailable")


if __name__ == "__main__":
    main()
