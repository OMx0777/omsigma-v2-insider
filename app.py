import json
import urllib.request
import re
import traceback
import gzip
import time
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

# In-memory cache to prevent spamming upstream and make responses instant (<5ms)
# Format: { TICKER: {"timestamp": float, "data": dict, "compressed": bytes} }
CACHE = {}
CACHE_TTL = 30  # 30 seconds cache


def fetch_insider_nextjs(ticker):
    """Fetches full options chain from InsiderFinance Next.js payload."""
    ticker = ticker.upper()
    now = time.time()

    # Check cache
    if ticker in CACHE and (now - CACHE[ticker]["timestamp"]) < CACHE_TTL:
        return CACHE[ticker]["data"], CACHE[ticker]["compressed"]

    try:
        # Step 1: Get the dynamic Build ID from the target page
        page_url = (
            f"https://www.insiderfinance.io/gamma-exposure/{ticker}"
            if ticker != "SPY"
            else "https://www.insiderfinance.io/gamma-exposure"
        )
        req = urllib.request.Request(
            page_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")

        m = re.search(r'"buildId":"([^"]+)"', html)
        if not m:
            return {"error": "Could not find Next.js Build ID"}, None
        build_id = m.group(1)

        # Step 2: Fetch the static JSON data payload
        if ticker == "SPY":
            url = f"https://www.insiderfinance.io/_next/data/{build_id}/gamma-exposure.json"
        else:
            url = f"https://www.insiderfinance.io/_next/data/{build_id}/gamma-exposure/{ticker}.json"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        raw_bytes = urllib.request.urlopen(req, timeout=20).read()
        data = json.loads(raw_bytes.decode("utf-8"))

        options = data.get("pageProps", {}).get("initialData", {}).get("options", [])
        spot = data.get("pageProps", {}).get("initialData", {}).get("spot", 0)
        timestamp = data.get("pageProps", {}).get("initialData", {}).get("timestamp", "")

        result = {
            "ticker": ticker,
            "spot": spot,
            "timestamp": timestamp,
            "total_contracts": len(options),
            "options": options,
        }

        # Pre-compress with gzip for lightning fast delivery
        json_bytes = json.dumps(result).encode("utf-8")
        compressed = gzip.compress(json_bytes)

        # Store in cache
        CACHE[ticker] = {
            "timestamp": now,
            "data": result,
            "compressed": compressed,
        }

        return result, compressed

    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc()}, None


@app.after_request
def add_cors_headers(response):
    """Enable CORS so any web app, TradingView widget, or client can call this API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept-Encoding, Authorization"
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": time.time()})


@app.route("/")
@app.route("/api/options")
def get_options():
    ticker = request.args.get("ticker", "").strip().upper()

    # If no ticker supplied and visiting root "/", return info
    if not ticker and request.path == "/":
        return jsonify({
            "name": "OMSIGMA Insider Options Chain API",
            "status": "online",
            "host": "Hugging Face Spaces (24/7 Free)",
            "endpoints": [
                "/?ticker=SPX",
                "/api/options?ticker=SPX",
                "/?ticker=SPY",
                "/health"
            ]
        })

    if not ticker:
        ticker = "SPX"

    result, compressed = fetch_insider_nextjs(ticker)

    if "error" in result and not compressed:
        return jsonify(result), 500

    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" in accept_encoding and compressed:
        response = Response(compressed, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Cache-Control"] = "public, max-age=30"
        return response

    return jsonify(result)


if __name__ == "__main__":
    # Hugging Face Spaces listens on port 7860
    import os
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)
