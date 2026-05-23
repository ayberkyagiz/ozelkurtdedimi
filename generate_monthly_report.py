import csv
import os
from datetime import date, datetime

from PIL import Image, ImageDraw

from generate_card import ensure_fonts, load


W, H = 1200, 675
START_YEAR, START_MONTH = 2026, 5
ARCHIVE_FILE = "Kemal_Kilicdaroglu_Tarihi_Arsiv.csv"
MONTHLY_ARCHIVE_FILE = "monthly_archive.csv"
HISTORY_FILE = "history.csv"

AYLAR_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}
AYLAR_KISA = {
    1: "Oca", 2: "Şub", 3: "Mar", 4: "Nis", 5: "May", 6: "Haz",
    7: "Tem", 8: "Ağu", 9: "Eyl", 10: "Eki", 11: "Kas", 12: "Ara"
}


def month_iter(start_year: int, start_month: int, end_year: int, end_month: int):
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m == 13:
            y += 1
            m = 1


def read_records() -> dict[date, bool]:
    records = {}
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f, delimiter=";"):
                try:
                    d = datetime.strptime(row["Tarih"], "%d.%m.%Y").date()
                except Exception:
                    continue
                records[d] = row.get("Kürt Deme Durumu", "").strip().lower() == "dedi"

    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("spoke") != "Y":
                    continue
                try:
                    d = date.fromisoformat(row["date"])
                except Exception:
                    continue
                records[d] = row.get("kurt") == "Y"
    return records


def read_monthly_archive() -> dict[tuple[int, int], dict]:
    archive = {}
    if not os.path.exists(MONTHLY_ARCHIVE_FILE):
        return archive
    with open(MONTHLY_ARCHIVE_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                year = int(row["year"])
                month = int(row["month"])
                spoken = int(row["spoken"])
                yes = int(row["yes"])
                no = int(row["no"])
            except Exception:
                continue
            rate = (yes / spoken * 100) if spoken else None
            archive[(year, month)] = {"year": year, "month": month, "spoken": spoken, "yes": yes, "no": no, "rate": rate}
    return archive


def monthly_series(end_year: int, end_month: int) -> list[dict]:
    archive = read_monthly_archive()
    records = read_records()
    series = []
    for y, m in month_iter(START_YEAR, START_MONTH, end_year, end_month):
        month_records = [kurt for d, kurt in records.items() if d.year == y and d.month == m]
        if month_records:
            spoken = len(month_records)
            yes = sum(1 for kurt in month_records if kurt)
            no = spoken - yes
            rate = (yes / spoken * 100) if spoken else None
            series.append({"year": y, "month": m, "spoken": spoken, "yes": yes, "no": no, "rate": rate})
        else:
            series.append(archive.get((y, m), {"year": y, "month": m, "spoken": 0, "yes": 0, "no": 0, "rate": None}))
    return series


def pct_text(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}%"


def draw_center(draw, box, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (bbox[2] - bbox[0])) / 2
    y = box[1] + (box[3] - box[1] - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), text, font=font, fill=fill)


def draw_icon(draw, box, kind, color):
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    if kind == "total":
        draw.polygon([(x1 + 18, cy - 12), (cx + 4, cy - 22), (cx + 4, cy + 22), (x1 + 18, cy + 12)], fill=color)
        draw.rectangle((x1 + 12, cy - 14, x1 + 22, cy + 14), fill=color)
        draw.line((cx + 12, cy - 18, x2 - 14, cy - 30), fill=color, width=4)
        draw.line((cx + 12, cy + 18, x2 - 14, cy + 30), fill=color, width=4)
        draw.line((x1 + 30, cy + 14, x1 + 42, y2 - 10), fill=color, width=6)
    elif kind == "yes":
        draw.line((x1 + 18, cy + 4, cx - 4, cy + 24), fill=color, width=10)
        draw.line((cx - 4, cy + 24, x2 - 14, y1 + 18), fill=color, width=10)
    else:
        draw.line((x1 + 18, y1 + 18, x2 - 18, y2 - 18), fill=color, width=10)
        draw.line((x1 + 18, y2 - 18, x2 - 18, y1 + 18), fill=color, width=10)


def make_stat_card(draw, box, title, value, kind, fonts, accent):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=12, fill=(255, 255, 255), outline=(194, 202, 208), width=2)
    draw_center(draw, (x1 + 12, y1 + 10, x2 - 12, y1 + 44), title, fonts["caption"], (20, 33, 43))
    icon_box = (x1 + 22, y1 + 54, x1 + 96, y2 - 20)
    draw.rounded_rectangle(icon_box, radius=10, fill=(238, 242, 245), outline=(196, 205, 211))
    draw_icon(draw, icon_box, kind, accent)
    draw.text((x1 + 118, y1 + 70), str(value), font=fonts["big"], fill=(5, 12, 20))


def make_monthly_report(year: int, month: int, out: str = "monthly_report.png") -> str:
    fonts_raw = ensure_fonts()
    fonts = {
        "title": load(fonts_raw["bold"], 32),
        "section": load(fonts_raw["bold"], 24),
        "caption": load(fonts_raw["bold"], 16),
        "small": load(fonts_raw["medium"], 14),
        "tiny": load(fonts_raw["medium"], 12),
        "big": load(fonts_raw["bold"], 46),
        "rate": load(fonts_raw["bold"], 48),
    }

    series = monthly_series(year, month)
    current = series[-1]
    rate = current["rate"] or 0

    navy = (20, 55, 76)
    red = (177, 20, 43)
    green = (34, 138, 72)
    grey = (230, 234, 237)
    ink = (8, 18, 28)

    img = Image.new("RGB", (W, H), (242, 244, 246))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((10, 10, W - 10, H - 10), radius=26, fill=(250, 250, 248), outline=(206, 212, 216), width=2)
    draw.rounded_rectangle((10, 10, W - 10, 84), radius=24, fill=navy)
    title = f'KEMAL KILIÇDAROĞLU "KÜRT" KELİMESİ KULLANIM DURUMU ({AYLAR_TR[month].upper()} {year})'
    draw_center(draw, (20, 12, W - 20, 78), title, fonts["title"], (255, 255, 255))

    panel = (48, 92, W - 48, 318)
    draw.rounded_rectangle(panel, radius=14, fill=(255, 255, 255), outline=(196, 205, 211), width=2)
    draw.rounded_rectangle((48, 92, W - 48, 138), radius=12, fill=grey, outline=(196, 205, 211), width=1)
    subtitle = f"{AYLAR_TR[month].upper()} {year} ÖZETİ (SADECE KONUŞTUĞU GÜNLER)"
    draw_center(draw, (60, 96, W - 60, 134), subtitle, fonts["section"], ink)

    make_stat_card(draw, (62, 148, 332, 300), "AYLIK TOPLAM\nKONUŞTUĞU GÜN SAYISI", current["spoken"], "total", fonts, navy)
    make_stat_card(draw, (356, 148, 626, 300), 'AYLIK KONUŞUP "KÜRT"\nDEDİĞİ GÜN SAYISI', current["yes"], "yes", fonts, green)
    make_stat_card(draw, (650, 148, 920, 300), 'AYLIK KONUŞUP "KÜRT"\nDEMEDİĞİ GÜN SAYISI', current["no"], "no", fonts, red)

    rate_box = (946, 148, 1138, 300)
    draw.rounded_rectangle(rate_box, radius=12, fill=(255, 255, 255), outline=(194, 202, 208), width=2)
    draw_center(draw, (956, 158, 1128, 198), '"KÜRT" Kelimesi\nKullanım Oranı (%)', fonts["caption"], ink)
    draw.rounded_rectangle((974, 210, 1006, 284), radius=10, fill=(236, 239, 242), outline=(185, 195, 202))
    fill_h = int(70 * max(0, min(100, rate)) / 100)
    draw.rounded_rectangle((978, 282 - fill_h, 1002, 282), radius=8, fill=red)
    draw.text((1030, 222), pct_text(current["rate"]), font=fonts["rate"], fill=ink)

    chart = (90, 380, 1140, 578)
    draw.text((W / 2 - 255, 334), f"AYLIK KULLANIM ORANI (%) GRAFİĞİ (Mayıs 2026 - {AYLAR_TR[month]} {year})", font=fonts["section"], fill=ink)
    x1, y1, x2, y2 = chart
    for pct in range(0, 101, 20):
        y = y2 - (pct / 100) * (y2 - y1)
        color = (170, 174, 178) if pct == 50 else (218, 222, 225)
        draw.line((x1, y, x2, y), fill=color, width=2 if pct == 50 else 1)
        draw.text((42, y - 8), f"{pct}%", font=fonts["tiny"], fill=ink)
    draw.text((x1 + 8, y2 - (0.5 * (y2 - y1)) - 20), "%50 Sınırı", font=fonts["tiny"], fill=ink)
    draw.line((x1, y2, x2, y2), fill=(120, 130, 138), width=2)
    draw.line((x1, y1, x1, y2), fill=(120, 130, 138), width=2)

    if len(series) == 1:
        step = 1
    else:
        step = (x2 - x1) / (len(series) - 1)
    points = []
    for i, item in enumerate(series):
        x = x1 + i * step
        value = item["rate"] if item["rate"] is not None else 0
        y = y2 - (value / 100) * (y2 - y1)
        points.append((x, y, item))

    for p1, p2 in zip(points, points[1:]):
        draw.line((p1[0], p1[1], p2[0], p2[1]), fill=navy, width=4)
    for x, y, item in points:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=navy, outline=(255, 255, 255), width=2)
        if item is current:
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=red, width=4)
            draw.text((x - 18, y - 34), pct_text(item["rate"]), font=fonts["small"], fill=red)

    label_every = 1 if len(series) <= 18 else 2
    for i, (x, _y, item) in enumerate(points):
        if i % label_every != 0 and item is not current:
            continue
        label = f"{AYLAR_KISA[item['month']]}\n{item['year']}"
        draw_center(draw, (x - 28, y2 + 8, x + 28, y2 + 42), label, fonts["tiny"], ink)

    draw.text((48, H - 28), "CHP Verileri & Konuşma Analizi", font=fonts["small"], fill=ink)
    draw.text((W - 278, H - 28), "Kaynak: chp.org.tr/gundem/", font=fonts["small"], fill=ink)
    img.save(out, "PNG", quality=95)
    print(f"Saved: {out}")
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python generate_monthly_report.py <year> <month> [out]")
        raise SystemExit(1)
    make_monthly_report(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "monthly_report.png")
