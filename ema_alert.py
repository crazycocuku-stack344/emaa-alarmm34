#!/usr/bin/env python3
"""Midas altcoinleri için EMA7 / EMA25 kesişim alarmı (15 dk mum) -> ntfy.

Mum verisi önce Binance'ten, Binance'te olmayan coinler için MEXC'ten alınır.

Kullanım:
  NTFY_TOPIC=konu-adin python ema_alert.py          # tek seferlik kontrol
  NTFY_TOPIC=konu-adin python ema_alert.py --loop   # bilgisayarda sürekli çalıştır
  NTFY_TOPIC=konu-adin python ema_alert.py --test   # test bildirimi gönder

Sadece standart kütüphane kullanır, pip kurulumu gerekmez.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

INTERVAL = "15m"
INTERVAL_MS = 15 * 60 * 1000
FAST, SLOW = 7, 25
LOOKBACK = 3          # son kaç kapanmış mumda kesişim aranacak (geciken çalışmalara tolerans)
CANDLES = 300         # EMA'nın oturması için çekilen mum sayısı
MIN_QUOTE_VOL = float(os.environ.get("MIN_QUOTE_VOL", "200000"))  # 24s USDT hacim alt sınırı
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
HEARTBEAT_HOUR = 9    # her gün bu saatten (TR) sonraki ilk çalışmada "sistem çalışıyor" mesajı

# Öncelik sırasıyla veri kaynakları: (ad, adresler, TradingView öneki). İkisi de Binance uyumlu API kullanır.
SOURCES = [
    ("Binance", ["https://data-api.binance.vision", "https://api.binance.com"], "BINANCE"),
    ("MEXC", ["https://api.mexc.com"], "MEXC"),
]
TR = timezone(timedelta(hours=3))

# Midas kripto altcoin listesi (BTC, ETH, BNB, stablecoin ve altın tokenları hariç)
COINS = """
XRP SOL TRX HYPE DOGE LINK ADA XLM NEAR BCH UNI LTC AVAX SUI HBAR TAO CRO SHIB QNT PUMP OKB ENA AAVE
ONDO MNT DOT SKY ASTER ICP WLD WLFI PEPE ARB MORPHO VVV POL KAS ALGO JUP LIT JST PI RENDER ATOM FIL
CAKE AERO INJ VET ETHFI XDC APT ZRO FLR PENGU PYTH CRV TRUMP STX SEI NEXO RAY FET VIRTUAL XPL TIA
PENDLE LDO SPX FF BTT KITE IMX BONK SUN GRT GNO STRK ENS OP JTO SYRUP CFX AR COMP FLOKI JASMY TWT
IOTA WIF EIGEN 2Z THETA KAIA AXS XCN MINA NEO MANA FARTCOIN MET CHZ GRASS VSN DEXE APE SENT 1INCH
EGLD H ZK SAND ZEN SUPER PLUME DYDX GALA S BEAM QTUM HUMA ORCA ORDI COW NMR KSM SKR CFG W SNX CHIP
WAL LPT RED ZETA BERA KAITO HOT BIO IP SUSHI ROSE TURBO BOME QUBIC LINEA AXL CKB ASTR IO 0G ACH PHA
ENJ GPS BLUR TRB SOMI COAI AMP BRETT ALLO G PNUT RAVE TOSHI ARK BARD NOT ANKR MEW RON MOG MOODENG
CSPR ME MASK EDU MERL MOVE COTI REZ NEIRO MEME SAHARA SKYAI BLEND UMA LAYER STEEM ZORA CORE MANTA
CATI VANA BEAT ACX ARKM SKL FOGO METIS CTSI USUAL CETUS EDEN GMT DEGEN AUCTION CYBER HOME DOGS API3
AEVO SOLV AIXBT ZKC AUDIO QANX BREV SIREN INIT TNSR PRL ENSO ROBO WCT XAI SPELL GOAT NAORIS SAPIEN
ANIME CAT MAGIC NEWT ARPA ORDER LUMIA KAT BASED RAD KERNEL RARE BMT OGN ARIA DYM SXT STORJ BB SAGA
WET SHELL HMSTR BEL ACT OPN VINE TREE SCR PRCL ZEUS ELIZAOS ATM BAR PSG ASR PORTO LAZIO JUV CITY ACM
""".split()

# Midas'taki sembol borsada farklıysa burada eşle (kaynak adı -> {Midas sembolü: borsa sembolü})
ALIASES = {"Binance": {"BTT": "BTTC"}}


def api(hosts, path, params=None):
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    last_err = None
    for host in hosts:
        try:
            req = urllib.request.Request(host + path + query, headers={"User-Agent": "ema-alert/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:  # sıradaki host'u dene
            last_err = e
    raise last_err


def notify(title, message, tags="", priority="default", click=None):
    headers = {"Title": title, "Tags": tags, "Priority": priority}  # başlık ASCII olmalı
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}", data=message.encode("utf-8"), headers=headers, method="POST"
    )
    urllib.request.urlopen(req, timeout=15).read()


def ema(values, n):
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def find_crosses(closes):
    """Son LOOKBACK mumda kesişimleri döndürür: [("AL"|"SAT", mum_indeksi), ...]"""
    e_fast, e_slow = ema(closes, FAST), ema(closes, SLOW)
    found = []
    for i in range(max(SLOW, len(closes) - LOOKBACK), len(closes)):
        prev = e_fast[i - 1] - e_slow[i - 1]
        cur = e_fast[i] - e_slow[i]
        if prev <= 0 < cur:
            found.append(("AL", i))
        elif prev >= 0 > cur:
            found.append(("SAT", i))
    return found, e_fast, e_slow


def load_markets():
    """Her kaynak için {sembol: 24s USDT hacmi}. Bir kaynak erişilemezse boş döner."""
    markets = {}
    for name, hosts, _ in SOURCES:
        try:
            tickers = api(hosts, "/api/v3/ticker/24hr")
            markets[name] = {
                t["symbol"]: float(t.get("quoteVolume") or 0)
                for t in tickers
                if t["symbol"].endswith("USDT")
            }
        except Exception as e:
            print(f"{name} listesi alinamadi: {e}")
            markets[name] = {}
    return markets


def pick_sources(markets):
    """Her coin için ilk uygun kaynağı seçer (hacim yeterliyse)."""
    chosen, missing, illiquid = [], [], []
    for coin in COINS:
        pick, seen_any = None, False
        for name, hosts, tv in SOURCES:
            sym = ALIASES.get(name, {}).get(coin, coin) + "USDT"
            if sym in markets[name]:
                seen_any = True
                if markets[name][sym] >= MIN_QUOTE_VOL:
                    pick = (coin, name, hosts, tv, sym)
                    break
        if pick:
            chosen.append(pick)
        elif seen_any:
            illiquid.append(coin)
        else:
            missing.append(coin)
    return chosen, missing, illiquid


def fetch_candles(item):
    """Sadece kapanmış mumları döndürür: (açılış_zamanları, kapanışlar)"""
    coin, name, hosts, tv, sym = item
    rows = None
    for attempt in range(3):
        try:
            rows = api(hosts, "/api/v3/klines", {"symbol": sym, "interval": INTERVAL, "limit": CANDLES})
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5)
    now_ms = int(time.time() * 1000)
    rows = [r for r in rows if int(r[0]) + INTERVAL_MS <= now_ms]  # kapanmamış son mumu at
    if len(rows) < SLOW + LOOKBACK + 5:
        return None
    return [int(r[0]) for r in rows], [float(r[4]) for r in rows]


def fmt(x):
    return f"{x:.8g}"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return None


def now_tr():
    return datetime.now(TR)


def throttled(meta, key, hours):
    """Aynı uyarıyı en fazla `hours` saatte bir göndermek için True/False döndürür."""
    now = time.time()
    if now - meta.get(key, 0) < hours * 3600:
        return True
    meta[key] = now
    return False


def safe_notify(*args, **kwargs):
    try:
        notify(*args, **kwargs)
        return True
    except Exception as e:
        print(f"Bildirim gonderilemedi: {e}")
        return False


def report_crash(err):
    """Script hata verirse haber ver (3 saatte bir en fazla)."""
    state = load_state()
    meta = state.setdefault("_meta", {}) if state is not None else {}
    if state is not None and throttled(meta, "crash_ts", 3):
        return
    safe_notify(
        "EMA alarmi HATA verdi",
        f"Script hata ile durdu: {str(err)[:200]}\nGitHub'da Actions sekmesinden log'a bak.",
        "rotating_light",
        priority="high",
    )
    if state is not None:
        STATE_FILE.write_text(json.dumps(state))


def run():
    state = load_state()
    first_run = state is None
    state = state or {}
    meta = state.setdefault("_meta", {})

    markets = load_markets()
    chosen, missing, illiquid = pick_sources(markets)
    per_source = {name: sum(1 for c in chosen if c[1] == name) for name, _, _ in SOURCES}
    print(
        f"Izlenen: {len(chosen)} ({', '.join(f'{k}: {v}' for k, v in per_source.items())}) | "
        f"Hicbir borsada yok: {len(missing)} | Hacmi dusuk: {len(illiquid)}"
    )
    if missing:
        print("Hicbir borsada yok:", " ".join(missing))
    if illiquid:
        print("Hacmi dusuk:", " ".join(illiquid))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_candles, chosen))

    problems = []
    failed_sources = [n for n, _, _ in SOURCES if not markets[n]]
    if failed_sources:
        problems.append("Veri alinamayan borsa: " + ", ".join(failed_sources))
    fetch_fail = sum(1 for d in results if not d)
    if chosen and fetch_fail > 0.3 * len(chosen):
        problems.append(f"{fetch_fail}/{len(chosen)} coinin mum verisi alinamadi")
    if problems and not throttled(meta, "warn_ts", 6):
        safe_notify("EMA alarmi uyari", "\n".join(problems), "warning")

    sent = 0
    for item, data in zip(chosen, results):
        if not data:
            continue
        coin, source, _, tv, sym = item
        times, closes = data
        crosses, e_fast, e_slow = find_crosses(closes)
        key = coin + "USDT"
        for kind, i in crosses:
            open_ms = times[i]
            seen = state.setdefault(key, {})
            if open_ms <= seen.get(kind, 0):
                continue
            seen[kind] = open_ms
            if first_run:
                continue  # ilk çalıştırmada eski kesişimler için bildirim atma
            close_time = datetime.fromtimestamp((open_ms + INTERVAL_MS) / 1000, TR).strftime("%H:%M")
            if kind == "AL":
                title, tags = f"{coin} AL sinyali (EMA7 yukari kesti)", "green_circle,chart_with_upwards_trend"
            else:
                title, tags = f"{coin} SAT sinyali (EMA7 asagi kesti)", "red_circle,chart_with_downwards_trend"
            body = (
                f"{coin}/USDT 15dk mum kapanışı ({close_time} TR)\n"
                f"Fiyat: {fmt(closes[i])}\n"
                f"EMA7: {fmt(e_fast[i])}  EMA25: {fmt(e_slow[i])}\n"
                f"Kaynak: {source}"
            )
            try:
                notify(title, body, tags, click=f"https://www.tradingview.com/chart/?symbol={tv}:{sym}")
                sent += 1
            except Exception as e:
                seen[kind] = open_ms - 1  # gönderilemedi, sonraki çalışmada tekrar dene
                print(f"Bildirim gonderilemedi ({sym}): {e}")

    meta["alerts"] = meta.get("alerts", 0) + sent
    today = now_tr().strftime("%Y-%m-%d")
    if first_run:
        meta["heartbeat"] = today
    elif now_tr().hour >= HEARTBEAT_HOUR and meta.get("heartbeat") != today:
        sources = ", ".join(f"{k}: {v}" for k, v in per_source.items())
        if safe_notify(
            "EMA alarmi calisiyor",
            f"Sistem çalışıyor. {len(chosen)} coin izleniyor ({sources}). "
            f"Son 24 saatte {meta['alerts']} sinyal gönderildi.",
            "green_heart",
            priority="low",
        ):
            meta["heartbeat"] = today
            meta["alerts"] = 0

    STATE_FILE.write_text(json.dumps(state))

    if first_run:
        notify(
            "EMA alarmi kuruldu",
            f"{len(chosen)} altcoin 15dk EMA7/EMA25 kesişimi için izleniyor. Bundan sonraki kesişimlerde bildirim gelecek.",
            "white_check_mark",
        )
        print("Ilk calistirma: baslangic durumu kaydedildi, kurulum bildirimi gonderildi.")
    print(f"Gonderilen bildirim: {sent}")


def main():
    if not NTFY_TOPIC:
        sys.exit("NTFY_TOPIC ortam değişkeni boş. Örn: NTFY_TOPIC=konu-adin python ema_alert.py")
    if "--test" in sys.argv:
        notify("Test bildirimi", "ntfy bağlantısı çalışıyor.", "tada")
        print("Test bildirimi gonderildi.")
        return
    if "--loop" in sys.argv:
        while True:
            try:
                run()
            except Exception as e:
                print("Hata:", e)
                report_crash(e)
            now = time.time()
            time.sleep(max(5, (now // 900 + 1) * 900 + 10 - now))  # sonraki 15 dk mum kapanışı + 10 sn
    else:
        try:
            run()
        except Exception as e:
            print("Hata:", e)
            report_crash(e)  # bildirim gönder; çıkış kodu 0, aksi halde durum kaydedilmez ve her 15 dk'da tekrar bildirim gelir


if __name__ == "__main__":
    main()
