import json
import os
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

SUFFIX = os.environ.get("NTFY_SUFFIX", "").strip()
if not SUFFIX:
    sys.exit("NTFY_SUFFIX tanimli degil (GitHub Secrets'a ekle).")

# interval, ntfy konu adi, mesajdaki etiket
TIMEFRAMES = [
    ("15m", f"ema7_25_15dk_{SUFFIX}", "15dk"),
    ("1h", f"ema7_25_1h_{SUFFIX}", "1s"),
    ("4h", f"ema7_25_4h_{SUFFIX}", "4s"),
    ("1d", f"ema7_25_1g_{SUFFIX}", "1g"),
]

HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
STATE_FILE = "state.json"


def http_get(path):
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(host + path, headers={"User-Agent": "ema-bot"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # sonraki host'u dene
            last = e
    raise last


def ema(values, period):
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def notify(topic, title, body, click):
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title,  # sadece ASCII
            "Tags": "chart_with_upwards_trend",
            "Priority": "high",
            "Click": click,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def check(args):
    symbol, interval, topic, label, state = args
    try:
        kl = http_get(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit=200")
    except Exception as e:
        print(f"HATA {symbol} {interval}: {e}")
        return None
    if len(kl) < 60:
        return None
    closes = [float(k[4]) for k in kl]  # son eleman = acik (canli) mum
    open_time = kl[-1][0]
    e7, e25 = ema(closes, 7), ema(closes, 25)
    crossed = e7[-2] <= e25[-2] and e7[-1] > e25[-1]
    key = f"{symbol}|{interval}"
    if crossed and state.get(key) != open_time:
        base = symbol[:-4]
        try:
            notify(
                topic,
                f"{base} {label} EMA7 > EMA25",
                f"{symbol} [{label}] EMA7, EMA25'i yukari kesti.\n"
                f"Fiyat: {closes[-1]}\nEMA7: {e7[-1]:.8g}  EMA25: {e25[-1]:.8g}",
                f"https://www.binance.com/en/trade/{base}_USDT",
            )
            print(f"BILDIRIM {symbol} {label}")
            return key, open_time
        except Exception as e:
            print(f"ntfy HATA {symbol}: {e}")
    return None


def main():
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE))
        except Exception:
            state = {}

    wanted = [
        l.strip().upper()
        for l in open("coins.txt", encoding="utf-8")
        if l.strip() and not l.startswith("#")
    ]
    wanted = list(dict.fromkeys(wanted))

    info = http_get("/api/v3/exchangeInfo")
    live = {
        s["symbol"]
        for s in info["symbols"]
        if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
    }
    symbols = [w + "USDT" for w in wanted if w + "USDT" in live]
    missing = [w for w in wanted if w + "USDT" not in live]
    if missing:
        print("BULUNAMADI (Binance spot USDT):", ", ".join(missing))
    print(f"{len(symbols)} coin taraniyor.")

    jobs = [
        (s, iv, topic, label, state)
        for s in symbols
        for iv, topic, label in TIMEFRAMES
    ]
    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(check, jobs):
            if res:
                state[res[0]] = res[1]

    json.dump(state, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
