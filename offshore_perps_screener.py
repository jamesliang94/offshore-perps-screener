"""
Offshore Perps Screener
-----------------------
Finds perpetual futures on big offshore exchanges (Binance, Bybit, OKX...)
with >= MIN_VOLUME_USD 24h volume whose underlying coin is NOT listed on
US exchanges (Coinbase, Kraken, Gemini).

Data source for perps: CoinGecko's public derivatives endpoint (works from
the US, unlike Binance's own futures API, which geoblocks US IPs).

Run:  pip install requests
      python offshore_perps_screener.py
"""

import os
import re
import requests

MIN_VOLUME_USD = 500_000_000

# Optional free CoinGecko "Demo" API key (recommended when running on GitHub Actions)
CG_KEY = os.environ.get("COINGECKO_API_KEY", "")

# Which perp venues to scan (as named by CoinGecko). Add or remove freely.
PERP_MARKETS = {
    "Binance (Futures)",
    # "Bybit (Futures)",
    # "OKX (Futures)",
    # "Bitget Futures",
}

TIMEOUT = 30
HEADERS = {"User-Agent": "perps-screener/1.0"}

# Exchange-specific tickers that mean the same coin
ALIASES = {"XBT": "BTC", "XDG": "DOGE", "XXBT": "BTC", "XETH": "ETH"}


def norm(sym: str) -> str:
    """Normalize a base symbol: uppercase, strip 1000x/1M multipliers, map aliases."""
    s = sym.upper().strip()
    s = re.sub(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])", "", s)
    return ALIASES.get(s, s)


# ---------- US exchange listings ----------

def coinbase_bases() -> set:
    r = requests.get("https://api.exchange.coinbase.com/products",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return {norm(p["base_currency"]) for p in r.json()
            if p.get("status") == "online"}


def kraken_bases() -> set:
    r = requests.get("https://api.kraken.com/0/public/AssetPairs",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    bases = set()
    for pair in r.json()["result"].values():
        ws = pair.get("wsname")  # e.g. "XBT/USD"
        if ws and "/" in ws:
            bases.add(norm(ws.split("/")[0]))
    return bases


def gemini_bases() -> set:
    r = requests.get("https://api.gemini.com/v1/symbols",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    quotes = ("usdt", "usdc", "gusd", "rlusd", "usd", "btc", "eth",
              "eur", "gbp", "sgd", "dai")
    bases = set()
    for s in r.json():
        s = s.lower()
        if s.endswith("perp"):  # skip Gemini's own perp symbols
            continue
        for q in quotes:
            if s.endswith(q) and len(s) > len(q):
                bases.add(norm(s[: -len(q)]))
                break
    return bases


# ---------- Offshore perps ----------

def offshore_perps() -> list:
    cg_headers = dict(HEADERS)
    if CG_KEY:
        cg_headers["x-cg-demo-api-key"] = CG_KEY
    r = requests.get("https://api.coingecko.com/api/v3/derivatives",
                     headers=cg_headers, timeout=TIMEOUT)
    r.raise_for_status()
    rows = []
    for t in r.json():
        if t.get("market") not in PERP_MARKETS:
            continue
        if t.get("contract_type") != "perpetual":
            continue
        vol = float(t.get("volume_24h") or 0)
        if vol < MIN_VOLUME_USD:
            continue
        base = norm(t.get("index_id") or t.get("symbol", ""))
        rows.append({
            "market": t["market"],
            "symbol": t.get("symbol"),
            "base": base,
            "volume_24h": vol,
            "funding": t.get("funding_rate"),
            "open_interest": t.get("open_interest"),
        })
    return rows


def main():
    us = {}
    for name, fn in [("Coinbase", coinbase_bases),
                     ("Kraken", kraken_bases),
                     ("Gemini", gemini_bases)]:
        try:
            us[name] = fn()
            print(f"Loaded {len(us[name]):>4} assets from {name}")
        except Exception as e:
            print(f"WARNING: couldn't load {name}: {e}")
            us[name] = set()
    us_all = set().union(*us.values())

    perps = offshore_perps()
    print(f"\n{len(perps)} perps with >= ${MIN_VOLUME_USD/1e6:.0f}M 24h volume\n")

    not_in_us = [p for p in perps if p["base"] not in us_all]
    not_in_us.sort(key=lambda p: p["volume_24h"], reverse=True)

    print("=== NOT listed on Coinbase / Kraken / Gemini ===")
    print(f"{'Symbol':<16}{'Venue':<22}{'24h Vol ($M)':>14}{'OI ($M)':>12}{'Funding %':>12}")
    for p in not_in_us:
        oi = f"{p['open_interest']/1e6:,.0f}" if p["open_interest"] else "-"
        fr = f"{p['funding']:.4f}" if p["funding"] is not None else "-"
        print(f"{p['symbol']:<16}{p['market']:<22}{p['volume_24h']/1e6:>14,.0f}{oi:>12}{fr:>12}")

    # Bonus: coins listed on some US venues but not all
    print("\n=== Listed on SOME US exchanges (partial) ===")
    for p in sorted(perps, key=lambda p: p["volume_24h"], reverse=True):
        if p["base"] in us_all:
            missing = [n for n, s in us.items() if p["base"] not in s]
            if missing:
                print(f"{p['symbol']:<16} missing from: {', '.join(missing)}")


if __name__ == "__main__":
    main()
