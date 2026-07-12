#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZamGelmedenAl — Groq AI Haber Yazarı
=====================================
RSS'ten haber çeker → Groq (Llama) ile HALKTAN BİRİ gibi, ZamGelmedenAl
tonunda ÖZGÜN Türkçe başlık+özet+mini analiz yazdırır → konuya uygun
ÜCRETSİZ görsel (Pexels, atıflı) ekler → news.json üretip GitHub'a push eder.
Site bu dosyayı okur; içerik artık kopya değil, kendi cümlelerimizle.

KURULUM (VPS'te bir kez):
  pip install requests
  export GROQ_API_KEY="gsk_..."          # console.groq.com (ücretsiz)
  export PEXELS_API_KEY="..."            # pexels.com/api (ücretsiz, opsiyonel)

ÇALIŞTIR:
  python3 news_ai_publisher.py --out /root/zamgelmedenal-data/news.json --push

CRON (saatte bir):
  0 * * * * cd /root/zamgelmedenal-data && GROQ_API_KEY=gsk_xxx PEXELS_API_KEY=xxx \
    python3 /root/dropbot/news_ai_publisher.py --out news.json --push >> /var/log/news_ai.log 2>&1

NOT: Anahtarları koda YAZMA; sadece ortam değişkeni/.env kullan.
"""

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# rss2json kaldırıldı; Google News RSS doğrudan çekilir (aracı servis yok)
# Yabancı (İngilizce) kaynaklardan çekilir; AI Türkçeye çevirip yeniden yazar.

# Kategori -> (Google News sorgusu, Pexels görsel anahtarı, site etiketi)
KATEGORILER = {
    "iyi":        ('"good news" OR "uplifting" OR "positive news" OR "heartwarming"', "hope nature volunteer", "İyi Haber"),
    "ekonomi":    ("economy markets inflation global finance", "economy finance business", "Ekonomi"),
    "teknoloji":  ("technology artificial intelligence gadget innovation", "technology ai gadget", "Teknoloji"),
    "otomobil":   ("automotive electric vehicle car news", "car automobile electric", "Otomobil"),
    "motosiklet": ("motorcycle motorbike news", "motorcycle motorbike", "Motosiklet"),
    "moda":       ("fashion design collection runway trend", "fashion style runway", "Moda"),
    "belgesel":   ("documentary nature discovery wildlife", "documentary nature ocean", "Belgesel"),
    "pati":       ('cat dog "animal rescue" pets shelter', "cat dog pet rescue", "Patili Dostlar"),
    "spor":       ("sports football basketball athletics", "sport athlete stadium", "Spor"),
    "gastronomi": ("food gastronomy cuisine chef restaurant", "food gastronomy cuisine", "Gastronomi"),
}
HER_KATEGORI = int(os.environ.get("HABER_SAYISI", "6"))  # kategori başına üretilecek haber

SYSTEM_PROMPT = (
    "Sen 'ZamGelmedenAl' adlı Türk haber sitesinin editörüsün. Sana genellikle "
    "İNGİLİZCE/yabancı bir haber başlığı ve özeti verilecek. Görevin: "
    "1) Haberi TÜRKÇEYE çevir, 2) ANCAK birebir/kelime kelime çeviri YAPMA — "
    "anlam bütünlüğünü ve olguları KORUYARAK, ifadeleri ve cümle yapısını değiştirip "
    "KENDİ CÜMLELERİNLE yeniden yaz (telif ve intihal riskini sıfırla). "
    "Halktan biri gibi samimi, akıcı, anlaşılır bir dil kullan; abartma, tıklama tuzağı kurma "
    "ama merak uyandır. Kısa bir mini-analiz/yorum ekle (okura ne ifade ediyor). "
    "Sayı, isim, yer gibi olguları DEĞİŞTİRME; sadece dili yeniden kur. "
    "Emin olmadığın bilgiyi uydurma. SADECE şu JSON'u döndür, başka hiçbir şey yazma: "
    '{"baslik":"...","ozet":"...","etiket":"..."}. '
    "baslik en fazla 90 karakter, ozet 2-3 cümle (en fazla 240 karakter), "
    "etiket tek kelime (ör. Dünya, Avrupa, ABD, Analiz, Gündem)."
)


def temizle(s: str) -> str:
    s = _html.unescape(s or "")
    s = re.sub(r"<[^>]*>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def rss_cek(sorgu: str):
    """Google News RSS'ini DOĞRUDAN çeker ve XML'i parse eder (aracı servis yok).
    Yabancı (İngilizce) edisyon; AI Türkçeye çevirip yeniden yazar."""
    import xml.etree.ElementTree as ET
    url = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en".format(quote(sorgu))
    try:
        r = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        r.raise_for_status()
        kok = ET.fromstring(r.content)
    except Exception as ex:
        print(f"  RSS cekme hatasi: {ex}")
        return []
    out = []
    for it in kok.iter("item"):
        t = temizle((it.findtext("title") or ""))
        desc = temizle((it.findtext("description") or ""))[:400]
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        # kaynak: baslik sonundaki " - Kaynak" kismini ayikla
        src = "Haber Kaynagi"
        p = t.rfind(" - ")
        if p > 10:
            src = t[p + 3:].strip()
            t = t[:p].strip()
        # source etiketi de olabilir
        src_el = it.find("source")
        if src_el is not None and (src_el.text or "").strip():
            src = src_el.text.strip()
        if not t:
            continue
        out.append({
            "ham_baslik": t,
            "ham_ozet": desc,
            "kaynak": temizle(src)[:60],
            "url": link,
            "pub": pub,
        })
    return out


def groq_yaz(ham_baslik: str, ham_ozet: str):
    """Groq ile özgün başlık+özet+etiket üret. Hata olursa None döner."""
    if not GROQ_KEY:
        return None
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.7,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[Yabancı kaynak — Türkçeye çevirip yeniden yaz]\nHam başlık: {ham_baslik}\nHam özet: {ham_ozet}"},
        ],
    }
    for deneme in range(3):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=40,
            )
            if r.status_code == 429:  # oran limiti — bekle
                time.sleep(3 + deneme * 3)
                continue
            r.raise_for_status()
            içerik = r.json()["choices"][0]["message"]["content"]
            veri = json.loads(içerik)
            b = temizle(veri.get("baslik", ""))[:110]
            o = temizle(veri.get("ozet", ""))[:260]
            e = temizle(veri.get("etiket", "Gündem"))[:24] or "Gündem"
            if b and o:
                return {"baslik": b, "ozet": o, "etiket": e}
        except Exception as ex:
            print(f"  groq hata ({deneme+1}): {ex}")
            time.sleep(2)
    return None


# --- Görsel: Pexels (ücretsiz, atıflı) + kategori yedek havuzu ---
UNSPLASH = "https://images.unsplash.com/photo-{}?w=600&q=60"
YEDEK_GORSEL = {
    "iyi": ["1505118380757-91f5f5632de0", "1509391366360-2e959784a276"],
    "ekonomi": ["1590283603385-17ffb3a7f29f", "1611974789855-9c2a0a7236a3"],
    "teknoloji": ["1485827404703-89b55fcc595e", "1518770660439-4636190af475"],
    "otomobil": ["1503376780353-7e6692767b70", "1502877338535-766e1452684a"],
    "motosiklet": ["1558981403-c5f9899a28bc", "1568772585407-9361f9bf3a87"],
    "moda": ["1515886657613-9f3515b0c78f", "1445205170230-053b83016050"],
    "belgesel": ["1544551763-46a013bb70d5", "1462331940025-496dfbfc7564"],
    "pati": ["1543466835-00a7907e9de1", "1514888286974-6c03e2ca1dba"],
    "spor": ["1461896836934-ffe607ba8211", "1517649763962-0c623066013b"],
    "gastronomi": ["1504674900247-0877df9cc836", "1476224203421-9ac39bcb3327"],
}


def gorsel_sec(kategori: str, anahtar: str, idx: int):
    """Pexels'ten atıflı ücretsiz foto; olmazsa kategori yedek havuzu."""
    if PEXELS_KEY:
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_KEY},
                params={"query": anahtar, "per_page": 12, "orientation": "landscape", "locale": "tr-TR"},
                timeout=20,
            )
            fotos = r.json().get("photos", [])
            if fotos:
                f = fotos[idx % len(fotos)]
                return f["src"]["landscape"], f"Pexels · {f.get('photographer','')}".strip(" ·")
        except Exception:
            pass
    hav = YEDEK_GORSEL.get(kategori, YEDEK_GORSEL["iyi"])
    return UNSPLASH.format(hav[idx % len(hav)]), "Lisanslı stok (temsilî)"


def gorecel(pub: str) -> str:
    try:
        dt = datetime(*time.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")[:6], tzinfo=timezone.utc)
    except Exception:
        return "Bugün"
    dk = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if dk < 60:
        return f"{max(dk,1)} dk önce"
    if dk < 1440:
        return f"{dk//60} saat önce"
    return f"{dk//1440} gün önce"


def kategori_uret(key: str):
    sorgu, gorsel_anahtar, etiket_ana = KATEGORILER[key]
    ham = rss_cek(sorgu)
    if not ham:
        print(f"  {key}: RSS boş")
        return []
    sonuc = []
    for i, x in enumerate(ham):
        if len(sonuc) >= HER_KATEGORI:
            break
        y = groq_yaz(x["ham_baslik"], x["ham_ozet"])
        if not y:
            continue  # AI yazamadıysa o haberi atla (kopya yayımlama)
        img, img_atif = gorsel_sec(key, gorsel_anahtar, i)
        sonuc.append({
            "t": y["baslik"],
            "d": y["ozet"],
            "k": y["etiket"] or etiket_ana,
            "s": "🌐 " + (x["kaynak"] or "Yabancı Kaynak") + " · çeviri/derleme",
            "time": gorecel(x["pub"]),
            "date": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M"),
            "img": img,
            "url": x["url"],          # doğrulama için orijinal kaynağa gider
            "gercek": True,
            "temsili": True,          # görsel temsilî (stok/atıflı)
            "analiz": True,           # ✍️ ZamGelmedenAl analizi rozeti
        })
        time.sleep(0.4)  # Groq oran limitine nazik ol
    print(f"  {key}: {len(sonuc)} özgün haber üretildi")
    return sonuc


def git_push(repo: str):
    for c in (["git", "add", "news.json"],
              ["git", "commit", "-m", f"news: AI güncelleme {datetime.now():%Y-%m-%d %H:%M}"],
              ["git", "push"]):
        r = subprocess.run(c, cwd=repo, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print("git hata:", r.stderr.strip())
            return
    print("GitHub'a push edildi.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--sadece", help="tek kategori test et (ör. teknoloji)")
    args = ap.parse_args()

    if not GROQ_KEY:
        print("HATA: GROQ_API_KEY tanımlı değil. console.groq.com'dan ücretsiz al.")
        sys.exit(1)

    keys = [args.sadece] if args.sadece else list(KATEGORILER)
    veri = {}
    for k in keys:
        print(f"[{k}] işleniyor…")
        items = kategori_uret(k)
        if items:
            veri[k] = items

    if not veri:
        print("Hiç haber üretilemedi (mevcut news.json korunur).")
        sys.exit(0)

    from pathlib import Path
    p = Path(args.out).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)
    toplam = sum(len(v) for v in veri.values())
    print(f"✓ {toplam} özgün haber, {len(veri)} kategori -> {p}")

    if args.push:
        git_push(str(p.parent))


if __name__ == "__main__":
    main()
