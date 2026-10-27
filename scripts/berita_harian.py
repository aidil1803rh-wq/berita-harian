#!/usr/bin/env python3
"""Ambil RSS dari banyak portal, gabungkan berita kembar, tulis docs/berita.json."""
import json, re, time, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import feedparser, requests

WIB = ZoneInfo("Asia/Jakarta")
UA = "Mozilla/5.0 (compatible; RangkumanBerita/1.0)"
JAM_MAKS = 30                      # hanya berita dalam N jam terakhir
BATAS = {"sumbar": 6, "nasional": 5, "internasional": 5}
PKS_TETAP = 3                      # 3 berita terakhir sumbar.pks.id selalu tampil
PALESTINA_MAKS = 2

# (nama, bagian, [url feed kandidat, dicoba berurutan])
GN = "hl=id&gl=ID&ceid=ID:id"
SUMBER = [
    ("Google News", "nasional", [f"https://news.google.com/rss?{GN}"]),
    ("Google News", "internasional", [f"https://news.google.com/rss/headlines/section/topic/WORLD?{GN}"]),
    ("Antara", "nasional", ["https://www.antaranews.com/rss/terkini.xml"]),
    ("Antara", "internasional", ["https://www.antaranews.com/rss/dunia.xml"]),
    ("Kompas", "nasional", ["https://www.kompas.com/rss", "https://rss.kompas.com/"]),
    ("Tribunnews", "nasional", ["https://www.tribunnews.com/rss"]),
    ("Tempo", "nasional", ["https://rss.tempo.co/nasional", "https://www.tempo.co/rss/terbaru"]),
    ("Detik", "nasional", ["https://news.detik.com/berita/rss", "https://rss.detik.com/index.php/detikcom"]),
    ("Penting Banget", "nasional", ["https://pentingbanget.com/feed/"]),
    ("PKS Sumbar", "sumbar", ["https://sumbar.pks.id/feed/"]),
    ("Sipp Sumbar", "sumbar", ["https://sippsumbar.com/feed/"]),
    ("Babarito", "sumbar", ["https://babarito.com/feed/"]),
    ("Antara Sumbar", "sumbar", ["https://sumbar.antaranews.com/rss/terkini.xml"]),
    ("Padang Ekspres", "sumbar", ["https://padek.jawapos.com/feed", "https://padek.jawapos.com/rss"]),
    ("Tribun Padang", "sumbar", ["https://padang.tribunnews.com/rss"]),
    ("Sumbarkita", "sumbar", ["https://sumbarkita.id/feed/"]),
]
KATA_SUMBAR = re.compile(r"\b(sumbar|sumatera barat|padang|bukittinggi|payakumbuh|solok|agam|pariaman|mentawai|pasaman|tanah datar|mahyeldi)\b", re.I)
KATA_PALESTINA = re.compile(r"(palestin|kepalestinaan|\bgaza\b|\bal-?aqsa\b|\btepi barat\b|\bhamas\b|\brafah\b)", re.I)
KATA_DUNIA = re.compile(r"\b(israel|iran|as|amerika|trump|rusia|ukraina|china|tiongkok|asean|pbb|eropa|malaysia)\b", re.I)
STOP = set("yang dan di ke dari untuk dengan pada ini itu akan atas oleh juga tak tidak dalam sebagai hingga usai jadi bisa ada kini".split())


def waktu(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(time.mktime(t), timezone.utc) if t else None


def bersih(judul, sumber):
    judul = re.sub(r"\s+", " ", judul).strip()
    # Google News menambahkan " - Nama Media" di akhir judul
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", judul) if sumber == "Google News" else judul


def token(judul):
    return {w for w in re.findall(r"[a-z0-9]+", judul.lower()) if w not in STOP and len(w) > 2}


# Portal yang feed RSS-nya sering gagal/kosong dari server GitHub: cadangan lewat pencarian Google News
DOMAIN = {"Kompas": "kompas.com", "Tribunnews": "tribunnews.com", "Tempo": "tempo.co", "Antara": "antaranews.com",
          "Detik": "detik.com", "Padang Ekspres": "padek.jawapos.com", "Tribun Padang": "padang.tribunnews.com"}


def baca(url, nama, bagian, batas_waktu, gn=False):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    hasil = []
    for e in feed.entries[:40]:
        w = waktu(e)
        if not e.get("link") or not e.get("title"):
            continue
        if w and w < batas_waktu:
            continue
        asal = nama
        if nama == "Google News" or gn:
            asal = ((e.get("source") or {}).get("title") or nama).strip()
        hasil.append({"judul": bersih(e.title, "Google News" if (nama == "Google News" or gn) else nama),
                      "link": e.link, "sumber": asal, "bagian": bagian, "gn": "news.google.com" in e.link,
                      "waktu": (w or datetime.now(timezone.utc)).isoformat()})
    return hasil, len(feed.entries)


def ambil(nama, bagian, urls, batas_waktu, status):
    kunci = f"{nama} ({bagian})"
    catatan = "gagal: tidak ada alamat feed yang bisa dibuka"
    for url in urls:
        try:
            hasil, total = baca(url, nama, bagian, batas_waktu)
            if hasil:
                status[kunci] = f"ok, {len(hasil)} berita"
                return hasil
            catatan = "kosong: feed terbaca tapi tidak ada berita baru" if total else "kosong: feed tidak terbaca"
        except Exception as ex:  # satu sumber gagal tidak boleh menghentikan yang lain
            catatan = f"gagal: {type(ex).__name__}"
    # cadangan: cari berita situs itu lewat Google News
    if nama in DOMAIN and bagian != "internasional":
        try:
            q = requests.utils.quote(f"site:{DOMAIN[nama]} when:1d")
            hasil, _ = baca(f"https://news.google.com/rss/search?q={q}&{GN}", nama, bagian, batas_waktu, gn=True)
            if hasil:
                status[kunci] = f"ok via Google News, {len(hasil)} berita (feed asli: {catatan})"
                return hasil
        except Exception as ex:
            catatan += f"; cadangan gagal: {type(ex).__name__}"
    status[kunci] = catatan
    return []


def gabung(items):
    """Gabungkan judul mirip (Jaccard >= 0.5). Skor = jumlah sumber berbeda."""
    grup = []
    for it in items:
        tk = token(it["judul"])
        for g in grup:
            u = tk | g["tk"]
            if u and len(tk & g["tk"]) / len(u) >= 0.5:
                g["items"].append(it); g["tk"] |= tk
                break
        else:
            grup.append({"tk": tk, "items": [it]})
    out = []
    for g in grup:
        sumber = {i["sumber"] for i in g["items"]}
        utama = max(g["items"], key=lambda i: (not i.get("gn"), i["waktu"]))  # utamakan link asli, bukan pengalihan Google
        out.append({**utama, "skor": len(sumber), "sumber": ", ".join(sorted(sumber))[:60]})
    return sorted(out, key=lambda x: (x["skor"], x["waktu"]), reverse=True)


def pilih(daftar, n, dipakai):
    hasil = []
    for it in daftar:
        if it["link"] in dipakai:
            continue
        hasil.append(it); dipakai.add(it["link"])
        if len(hasil) >= n:
            break
    return hasil


def tampil(it):
    return {"judul": it["judul"], "link": it["link"], "sumber": it["sumber"]}


def main():
    sekarang = datetime.now(timezone.utc)
    batas_waktu = sekarang - timedelta(hours=JAM_MAKS)
    status, semua = {}, []
    for nama, bagian, urls in SUMBER:
        semua += ambil(nama, bagian, urls, batas_waktu, status)

    dipakai = set()
    # --- Sumbar: 3 terbaru PKS Sumbar, lalu berita sumbar terpopuler
    pks = sorted([i for i in semua if i["sumber"] == "PKS Sumbar"], key=lambda i: i["waktu"], reverse=True)
    sumbar = pilih(pks, PKS_TETAP, dipakai)
    calon = [i for i in semua if i["bagian"] == "sumbar" or KATA_SUMBAR.search(i["judul"])]
    calon = [i for i in calon if i["sumber"] != "PKS Sumbar"]
    sumbar += pilih(gabung(calon), BATAS["sumbar"] - len(sumbar), dipakai)

    # --- Internasional: maks 2 Palestina, sisanya berita dunia terpopuler
    pal = [i for i in semua if KATA_PALESTINA.search(i["judul"])]
    intl_pal = pilih(gabung(pal), PALESTINA_MAKS, dipakai)
    dunia = [i for i in semua if i["bagian"] == "internasional" or KATA_DUNIA.search(i["judul"])]
    dunia = [i for i in dunia if not KATA_PALESTINA.search(i["judul"]) and not KATA_SUMBAR.search(i["judul"])]
    intl = pilih(gabung(dunia), BATAS["internasional"] - len(intl_pal), dipakai) + intl_pal

    # --- Nasional: sisanya
    nas = [i for i in semua if i["bagian"] == "nasional"
           and not KATA_PALESTINA.search(i["judul"]) and not KATA_SUMBAR.search(i["judul"])]
    nasional = pilih(gabung(nas), BATAS["nasional"], dipakai)

    data = {
        "dibuat": sekarang.astimezone(WIB).isoformat(timespec="minutes"),
        "tanggal": sekarang.astimezone(WIB).strftime("%d %B %Y"),
        "bagian": {"sumbar": [tampil(i) for i in sumbar],
                   "nasional": [tampil(i) for i in nasional],
                   "internasional": [tampil(i) for i in intl]},
        "status": status,
    }
    Path("docs").mkdir(exist_ok=True)
    Path("docs/berita.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    gagal = [k for k, v in status.items() if not v.startswith("ok")]
    print(f"Selesai. {sum(len(v) for v in data['bagian'].values())} berita. Bermasalah: {gagal or 'tidak ada'}")
    for k, v in status.items():
        print(" -", k, ":", v)


if __name__ == "__main__":
    sys.exit(main())
