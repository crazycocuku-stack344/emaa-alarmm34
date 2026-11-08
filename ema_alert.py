#!/usr/bin/env python3
"""Midas altcoinleri için EMA7 / EMA25 kesişim alarmı (15 dk mum) -> ntfy.

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

# data-api.binance.vision herkese açık piyasa verisi verir ve ABD IP'lerinde de çalışır
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
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

# Midas'taki sembol Binance'te farklıysa burada eşle
ALIASES = {"BTT": "BTTC"}


def api(path, params=None):
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    last_err = None
    for host in HOSTS:
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


def fetch_candles(symbol):
    """Sadece kapanmış mumları döndürür: (açılış_zamanları, kapanışlar)"""
    for attempt in range(3):
        try:
            rows = api("/api/v3/klines", {"symbol": symbol, "interval": INTERVAL, "limit": CANDLES})
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5)
    now_ms = int(time.time() * 1000)
    rows = [r for r in rows if r[6] < now_ms]  # kapanmamış son mumu at
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


def run():
    state = load_state()
    first_run = state is None
    state = state or {}

    tickers = api("/api/v3/ticker/24hr")
    volume = {t["symbol"]: float(t["quoteVolume"]) for t in tickers if t["symbol"].endswith("USDT")}

    symbols, missing, illiquid = [], [], []
    for c in COINS:
        sym = ALIASES.get(c, c) + "USDT"
        if sym not in volume:
            missing.append(c)
        elif volume[sym] < MIN_QUOTE_VOL:
            illiquid.append(c)
        else:
            symbols.append(sym)
    print(f"Izlenen: {len(symbols)} | Binance'te yok: {len(missing)} | Hacmi dusuk: {len(illiquid)}")
    if missing:
        print("Binance'te yok:", " ".join(missing))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_candles, symbols))

    sent = 0
    for sym, data in zip(symbols, results):
        if not data:
            continue
        times, closes = data
        crosses, e_fast, e_slow = find_crosses(closes)
        coin = sym[:-4]
        for kind, i in crosses:
            open_ms = times[i]
            seen = state.setdefault(sym, {})
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
                f"EMA7: {fmt(e_fast[i])}  EMA25: {fmt(e_slow[i])}"
            )
            try:
                notify(title, body, tags, click=f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}")
                sent += 1
            except Exception as e:
                seen[kind] = open_ms - 1  # gönderilemedi, sonraki çalışmada tekrar dene
                print(f"Bildirim gonderilemedi ({sym}): {e}")

    STATE_FILE.write_text(json.dumps(state))

    if first_run:
        notify(
            "EMA alarmi kuruldu",
            f"{len(symbols)} altcoin 15dk EMA7/EMA25 kesişimi için izleniyor. Bundan sonraki kesişimlerde bildirim gelecek.",
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
            now = time.time()
            time.sleep(max(5, (now // 900 + 1) * 900 + 10 - now))  # sonraki 15 dk mum kapanışı + 10 sn
    else:
        run()


if __name__ == "__main__":
    main()
