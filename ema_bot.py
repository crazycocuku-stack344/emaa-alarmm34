import json
import os
import sys
import time
import urllib.error
import urllib.request
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

SPOT_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
ALPHA_HOST = "https://www.binance.com"
ALPHA_LIST = "/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
ALPHA_KLINES = "/bapi/defi/v1/public/alpha-trade/klines"
HOUR_MS = 3_600_000
# Alpha'da 4s / 1g dogrudan gelmezse 1 saatliklardan uretilir (UTC'ye hizali)
AGG = {"4h": 4 * HOUR_MS, "1d": 24 * HOUR_MS}
MIN_CANDLES = 30
STATE_FILE = "state.json"
STATE = {}
UA = {"User-Agent": "Mozilla/5.0 (compatible; ema-bot)"}


def http_json(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (418, 429, 502, 503):
                time.sleep(1.5 * (i + 1))
                continue
            break
        except Exception as e:
            last = e
            time.sleep(0.5)
    raise last


# ---------- Spot ----------
def spot_get(path):
    last = None
    for host in SPOT_HOSTS:
        try:
            return http_json(host + path, retries=1)
        except Exception as e:  # sonraki host'u dene
            last = e
    raise last


def fetch_spot(symbol, interval):
    kl = spot_get(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit=200")
    return [(int(k[0]), float(k[4])) for k in kl]


# ---------- Alpha ----------
def alpha_klines(pair, interval, limit):
    d = http_json(
        f"{ALPHA_HOST}{ALPHA_KLINES}?symbol={pair}&interval={interval}&limit={limit}"
    )
    if not isinstance(d, dict) or str(d.get("code")) != "000000" or not d.get("data"):
        return []
    return [(int(k[0]), float(k[4])) for k in d["data"]]


def aggregate(rows, bucket_ms):
    out = {}
    for t, c in sorted(rows):
        out[t // bucket_ms * bucket_ms] = c  # kovadaki son kapanis
    return sorted(out.items())


def fetch_alpha(alpha_id, interval):
    last_err = None
    for quote in ("USDT", "USDC"):
        pair = f"{alpha_id}{quote}"
        rows = []
        try:
            rows = alpha_klines(pair, interval, 300)
        except Exception as e:
            last_err = e
        if not rows and interval in AGG:
            try:
                rows = aggregate(alpha_klines(pair, "1h", 1500), AGG[interval])
            except Exception as e:
                last_err = e
        if rows:
            return rows
    if last_err:
        raise last_err
    return []


def load_alpha(wanted):
    """Eksik coinler icin Alpha listesinde ayni sembolun en yuksek hacimlisini sec."""
    d = http_json(ALPHA_HOST + ALPHA_LIST)
    tokens = d.get("data") or []
    best = {}
    for t in tokens:
        sym = str(t.get("symbol", "")).upper()
        if sym not in wanted or t.get("offline"):
            continue
        try:
            vol = float(t.get("volume24h") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if sym not in best or vol > best[sym][0]:
            best[sym] = (vol, t)
    return {s: v for s, v in best.items()}


# ---------- Ortak ----------
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
            "Title": title.encode("ascii", "ignore").decode(),  # sadece ASCII
            "Tags": "chart_with_upwards_trend",
            "Priority": "high",
            "Click": click,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def get_rows(job):
    if job["kind"] == "spot":
        return fetch_spot(job["api"], job["interval"])
    return fetch_alpha(job["api"], job["interval"])


def check(job):
    label = job["label"]
    try:
        rows = sorted(get_rows(job))
    except Exception as e:
        print(f"HATA {job['name']} {job['interval']}: {e}")
        return None
    if len(rows) < MIN_CANDLES:
        return None
    closes = [c for _, c in rows]  # son eleman = acik (canli) mum
    open_time = rows[-1][0]
    e7, e25 = ema(closes, 7), ema(closes, 25)
    crossed = e7[-2] <= e25[-2] and e7[-1] > e25[-1]
    key = f"{job['id']}|{job['interval']}"
    if not (crossed and STATE.get(key) != open_time):
        return None

    if job["kind"] == "spot":
        title = f"{job['name']} {label} EMA7 > EMA25"
        head = f"{job['api']} [{label}] EMA7, EMA25'i yukari kesti."
    else:
        title = f"{job['name']} {label} EMA7 > EMA25 (Alpha)"
        head = (
            f"{job['name']} (Alpha, {job['chain']}) [{label}] EMA7, EMA25'i yukari kesti.\n"
            f"Kontrat: {job['contract']}"
        )
    body = head + f"\nFiyat: {closes[-1]:.10g}\nEMA7: {e7[-1]:.8g}  EMA25: {e25[-1]:.8g}"
    try:
        notify(job["topic"], title, body, job["link"])
        print(f"BILDIRIM {job['name']} {label}")
        return key, open_time
    except Exception as e:
        print(f"ntfy HATA {job['name']}: {e}")
    return None


def run_jobs(jobs, workers):
    done = {}
    if not jobs:
        return done
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(check, jobs):
            if res:
                done[res[0]] = res[1]
    return done


def main():
    global STATE
    if os.path.exists(STATE_FILE):
        try:
            STATE = json.load(open(STATE_FILE))
        except Exception:
            STATE = {}

    wanted = [
        l.strip().upper()
        for l in open("coins.txt", encoding="utf-8")
        if l.strip() and not l.startswith("#")
    ]
    wanted = list(dict.fromkeys(wanted))

    info = spot_get("/api/v3/exchangeInfo")
    live = {
        s["symbol"]
        for s in info["symbols"]
        if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
    }
    spot_syms = [w for w in wanted if w + "USDT" in live]
    missing = [w for w in wanted if w + "USDT" not in live]

    alpha_map = {}
    if missing:
        try:
            alpha_map = load_alpha(set(missing))
        except Exception as e:
            print(f"ALPHA listesi alinamadi: {e}")
    for sym, (vol, t) in alpha_map.items():
        print(
            f"ALPHA {sym} -> {t.get('alphaId')} | {t.get('name')} | "
            f"{t.get('chainName')} | hacim24h={vol:.0f}"
        )
    not_found = [m for m in missing if m not in alpha_map]
    if not_found:
        print("BULUNAMADI (spot ve Alpha):", ", ".join(not_found))

    spot_jobs, alpha_jobs = [], []
    for iv, topic, label in TIMEFRAMES:
        for w in spot_syms:
            spot_jobs.append(
                dict(
                    kind="spot", name=w, api=w + "USDT", id=w + "USDT",
                    interval=iv, topic=topic, label=label,
                    link=f"https://www.binance.com/en/trade/{w}_USDT",
                )
            )
        for sym, (_, t) in alpha_map.items():
            alpha_jobs.append(
                dict(
                    kind="alpha", name=sym, api=t["alphaId"], id=t["alphaId"],
                    interval=iv, topic=topic, label=label,
                    chain=t.get("chainName", "?"),
                    contract=t.get("contractAddress", "?"),
                    link="https://www.binance.com/en/alpha",
                )
            )
    print(f"{len(spot_syms)} spot + {len(alpha_map)} alpha coin taraniyor.")

    STATE.update(run_jobs(spot_jobs, 10))
    STATE.update(run_jobs(alpha_jobs, 4))
    json.dump(STATE, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
