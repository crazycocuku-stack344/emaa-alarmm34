#!/usr/bin/env python3
"""
EMA7 / EMA25 AL sinyali botu - MIDAS KRIPTO coinleri (15dk grafik, ANLIK)

- Mum kapanisini BEKLEMEZ: EMA7, EMA25'i yukari kestigi an AL bildirimi gonderir
- Sadece AL sinyali
- IZLEME_LISTESI: senin oncelikli coinlerin, sik taranir, bildirimde ⭐ olur
- MIDAS_COINLERI: Midas Kripto'daki kucuk/orta altcoinler (BTC, ETH, BNB, SOL gibi
  buyukler ve stable'lar haric), daha seyrek taranir
- Fiyat verisi Binance / MEXC'ten alinir (Midas'in acik bir API'si yok)
- Bildirim: ntfy

Sadece standart kutuphane kullanir, pip install gerekmez.
Gizli bilgi kodda durmaz: NTFY_TOPIC ortam degiskeninden okunur.
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

# ================== AYARLAR ==================
# GitHub'da: Settings > Secrets > Actions > NTFY_TOPIC olarak ekle.
# Kendi bilgisayarinda: export NTFY_TOPIC="eren-ema725-xxxx"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "BURAYA-KENDI-NTFY-KONUN")
# Istersen "diger Midas coinleri" ayri bir konuya gitsin (bos = ayni konu)
NTFY_TOPIC_DIGER = os.environ.get("NTFY_TOPIC_DIGER", "") or NTFY_TOPIC

# Senin oncelikli coinlerin (sadece sembol, USDT otomatik eklenir)
# Not: KGEN su an Midas listesinde gorunmuyor, yine de taranir.
IZLEME_LISTESI = [
    "STRK", "PLUME", "PUMP", "KGEN", "CYBER", "CASHCAT", "PONS",
    "LSK", "ONE", "EVAA", "ZEUS", "ARIA", "AKE", "PIEVERSE",
]

POLL_SANIYE = 15                # oncelikli liste kac saniyede bir kontrol edilsin
COOLDOWN_DK = 15                # ayni coine tekrar sinyal atmadan once bekleme

TARA_MIDAS = True               # Midas'taki diger coinleri de tara
MIDAS_TARAMA_SANIYE = 45        # diger Midas coinleri kac saniyede bir taransin
VERI_YOK_BEKLE_DK = 10          # verisi bulunamayan coin kac dk sonra tekrar denensin

INTERVAL = "15m"
KISA_EMA = 7
UZUN_EMA = 25
MUM_SAYISI = 120
TR = timezone(timedelta(hours=3))

# 0 = sonsuza kadar calis. GitHub Actions icin workflow 352 veriyor.
CALISMA_SURESI_DK = int(os.environ.get("CALISMA_SURESI_DK", "0"))

# Binance'in public veri adresi GitHub (ABD) sunucularindan da acilir.
KAYNAKLAR = [
    ("Binance", "https://data-api.binance.vision"),
    ("Binance", "https://api.binance.com"),
    ("MEXC", "https://api.mexc.com"),
]

# Midas Kripto'da listelenen coinler (getmidas.com, Kasim 2026).
# Midas yeni coin eklerse buraya yazabilirsin.
MIDAS_COINLERI = """
    USDT BTC USDC ETH SOL XRP BNB NEAR SUI DOGE
    HYPE UNI WLD TRX AVAX ENA TIA ADA TRUMP LINK
    AAVE FIL LTC INJ DOT FET TAO STRK PUMP ARB
    BCH PEPE ONDO ME ZRO XLM OP PENGU PAXG APT
    W POL ASTER ALGO HBAR SEI QNT PYUSD MOVE AERO
    LIT SAND VIRTUAL AMP BLUR XPL PYTH LDO SXT WIF
    EIGEN RAY ICP ETHFI EURC CRV SHIB RAD USUAL CAKE
    PENDLE BERA JST ZK ACT BASED ZAMA NMR WLFI PONS
    JUP VVV GALA JTO DRV IO SUPER APE ATOM ARKM
    RENDER STX BONK FLOKI OKB WAL MNT AUDIO CHZ GRASS
    RAIN PLUME MINA PIEVERSE S MARSCOIN PHA CASHCAT MEGA ZEN
    SENT AXL AKE MORPHO KITE CHIP USELESS SIREN AXS GENIUS
    B3 GRT HUMA KAIA COMP MAGIC CFX SUN MET SKY
    TRB MANA SUSHI SHELL SOLV BTT FLOCK FARTCOIN BOME BEAM
    0G ENS ANIME KAS AR BIO ORCA ORDI OGN LINEA
    ACE VET SYRUP XDC KAITO LAYER IOTA CYBER IMX ZORA
    LPT API3 METIS MOG SPK BREV DOS INIT AEON RARE
    MANTA TURBO BLEND FF DEXE DOGS SNX PNUT COTI UMA
    SPELL ALLO SAGA LUMIA CFG LSK HOME LAZIO JASMY EDU
    WCT XCN SPX NEXO GMT SKR OPN AUCTION CTSI SKL
    SAHARA PRL ENSO TIBBIR SIGN QTUM WSTETH ROBO 2Z MEME
    REZ 1INCH TNSR ARPA MASK ATM DYM ANKR G CKB
    XAI ZKC ZETA ZEUS KAT GPS NEIRO RED KERNEL NEO
    TRAC GRVT ENJ MOODENG ARIA EGLD SOMI TWT GOAT TOSHI
    PI VSN DEGEN ORDER CATI VELVET BRETT ACH CYS MEW
    BARD CP SKYAI THETA AEVO DYDX CRO VINE ASR TREE
    ELIZAOS COW COAI BB ROSE STORJ HMSTR CITY BASECAT BEAT
    NOT EOS FLR CORE JUV WELL RAVE O STEEM ONE
    TUT MERL CSPR AIXBT VELO BEL PSG FOGO BAR TAG
    SCR CVC NEWT QUID SAPIEN NPC ACM EDEN KSM HOT
    SAFE H PORTO CETUS MAMO QUBIC BMT ARK NAORIS ASTR
    NOCK BNKR DIEM LMTS CMC20 CLANKER PLAY UP WET RON
    VANA SERV CAT GNO MEY FUN TIG SEDA DOGINME DRB
    OVPP ARG ACX AFC PRCL REPPO FAI POR REI BID
    KEYCAT QANX AUKI OWB VFY SUP NOICE ACA MIGGLES BAKE
    MKR OMNI IP BELIEVE OM TROY BNX SLF
""".split()

# Taranmasi anlamsiz olanlar: stable / sarmalanmis / altin / endeks
HARIC = {"USDT", "USDC", "PYUSD", "EURC", "WSTETH", "PAXG", "CMC20"}
# Buyuk coinler (BTC, ETH, BNB, SOL gibi) taranmaz. Istedigini listeden silebilirsin.
BUYUK_COINLER = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "LINK", "AVAX",
    "LTC", "BCH", "XLM", "DOT", "HYPE", "SUI", "UNI", "NEAR", "SHIB", "AAVE",
    "OKB", "CRO",
}
# =============================================

IZLEME_SET = set(IZLEME_LISTESI)
DIGER_COINLER = [c for c in dict.fromkeys(MIDAS_COINLERI)
                 if c not in IZLEME_SET and c not in HARIC and c not in BUYUK_COINLER]

durum = {}          # coin -> True ise EMA7 > EMA25
son_sinyal = {}     # coin -> son sinyal zamani (unix)
kaynak_sirasi = {}  # coin -> en son calisan kaynagin indeksi
veri_yok = {}       # coin -> veri bulunamadigi zaman (unix)


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "ema-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ema(degerler, periyot):
    k = 2 / (periyot + 1)
    sonuc = [degerler[0]]
    for d in degerler[1:]:
        sonuc.append(d * k + sonuc[-1] * (1 - k))
    return sonuc


def mumlari_getir(coin):
    """Kaynaklari dener, calisani hatirlar. (kaynak, mumlar) doner."""
    if time.time() - veri_yok.get(coin, 0) < VERI_YOK_BEKLE_DK * 60:
        return None, None
    sembol = f"{coin}USDT"
    ilk = kaynak_sirasi.get(coin, 0)
    sira = [ilk] + [i for i in range(len(KAYNAKLAR)) if i != ilk]
    for i in sira:
        ad, taban = KAYNAKLAR[i]
        try:
            url = f"{taban}/api/v3/klines?symbol={sembol}&interval={INTERVAL}&limit={MUM_SAYISI}"
            veri = http_get(url)
            if isinstance(veri, list) and len(veri) > UZUN_EMA + 5:
                kaynak_sirasi[coin] = i
                veri_yok.pop(coin, None)
                return ad, veri
        except Exception:
            continue
    veri_yok[coin] = time.time()
    return None, None


def hesapla(mumlar):
    """ACIK (canli) mum dahil EMA'lari hesaplar."""
    kapanislar = [float(m[4]) for m in mumlar]   # son eleman = canli fiyat
    hacimler = [float(m[5]) for m in mumlar]
    e_kisa = ema(kapanislar, KISA_EMA)
    e_uzun = ema(kapanislar, UZUN_EMA)
    ort_hacim = sum(hacimler[-21:-1]) / 20 if len(hacimler) > 21 else 0
    return {
        "fiyat": kapanislar[-1],
        "ema_kisa": e_kisa[-1],
        "ema_uzun": e_uzun[-1],
        "ustte": e_kisa[-1] > e_uzun[-1],
        "kapanmis_ustte": e_kisa[-2] > e_uzun[-2],   # son kapanmis mumdaki durum
        "hacim_orani": hacimler[-1] / ort_hacim if ort_hacim else 0,
        "mum_acilis": int(mumlar[-1][0]),
    }


def bildirim_gonder(baslik, mesaj, topic):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=mesaj.encode("utf-8"),
            headers={
                "Title": baslik.encode("utf-8"),
                "Priority": "high",
                "Tags": "chart_with_upwards_trend",
            },
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("Bildirim hatasi:", e)


def fiyat_yaz(f):
    return f"{f:.8f}".rstrip("0").rstrip(".")


def tek_coin_kontrol(coin):
    kaynak, mumlar = mumlari_getir(coin)
    if not mumlar:
        return coin, None, None
    return coin, kaynak, hesapla(mumlar)


def tara(coinler, oncelikli):
    with ThreadPoolExecutor(max_workers=8) as havuz:
        sonuclar = list(havuz.map(tek_coin_kontrol, coinler))

    for coin, kaynak, s in sonuclar:
        if s is None:
            continue

        onceki = durum.get(coin)
        durum[coin] = s["ustte"]

        if onceki is None:
            # Ilk tur. Oncelikli listede bu mumda olmus kesisimi de yakala;
            # diger coinlerde sadece durumu kaydet.
            onceki = s["kapanmis_ustte"] if oncelikli else s["ustte"]

        # AL sinyali: onceden EMA7 altindaydi, simdi ustune gecti
        if not (onceki is False and s["ustte"]):
            continue

        simdi = time.time()
        if simdi - son_sinyal.get(coin, 0) < COOLDOWN_DK * 60:
            continue
        son_sinyal[coin] = simdi

        saat = datetime.now(TR).strftime("%H:%M:%S")
        mum_saat = datetime.fromtimestamp(s["mum_acilis"] / 1000, TR).strftime("%H:%M")
        yildiz = "⭐ " if oncelikli else ""
        baslik = f"🟢📈 {yildiz}{coin} AL sinyali (EMA7 yukari kesti)"
        mesaj = (
            f"{coin}/USDT 15dk ANLIK kesişim ({saat} TR, mum {mum_saat})\n"
            f"Fiyat: {fiyat_yaz(s['fiyat'])}\n"
            f"EMA7: {fiyat_yaz(s['ema_kisa'])}  EMA25: {fiyat_yaz(s['ema_uzun'])}\n"
            f"Hacim: son 20 mum ortalamasının {s['hacim_orani']:.1f}x'i\n"
            f"Not: mum henüz kapanmadı, kesişim geri dönebilir.\n"
            f"Kaynak: {kaynak}"
        )
        print(baslik, flush=True)
        bildirim_gonder(baslik, mesaj, NTFY_TOPIC if oncelikli else NTFY_TOPIC_DIGER)


def main():
    print("Bot basladi (anlik mod).", flush=True)
    print("Oncelikli:", ", ".join(IZLEME_LISTESI), flush=True)
    print(f"Diger Midas coinleri: {len(DIGER_COINLER)} adet "
          f"({'acik' if TARA_MIDAS else 'kapali'})", flush=True)
    if NTFY_TOPIC.startswith("BURAYA"):
        print("UYARI: NTFY_TOPIC ayarlanmamis, bildirim gitmez!", flush=True)
    bitis = time.time() + CALISMA_SURESI_DK * 60 if CALISMA_SURESI_DK else None
    son_diger = 0
    ilk_diger = True
    while True:
        basla = time.time()
        try:
            tara(IZLEME_LISTESI, True)
            if TARA_MIDAS and time.time() - son_diger >= MIDAS_TARAMA_SANIYE:
                son_diger = time.time()
                tara(DIGER_COINLER, False)
                if ilk_diger:
                    ilk_diger = False
                    bulunan = sum(1 for c in DIGER_COINLER if c in durum)
                    print(f"Veri alinan Midas coini: {bulunan}/{len(DIGER_COINLER)}", flush=True)
        except Exception as e:
            print("Dongu hatasi:", e, flush=True)
        if bitis and time.time() >= bitis:
            print("Calisma suresi doldu, kapaniyor.", flush=True)
            break
        time.sleep(max(1, POLL_SANIYE - (time.time() - basla)))


if __name__ == "__main__":
    main()
