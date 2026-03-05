import os
import re
import json
import csv
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import tweepy

BASE = "https://chp.org.tr"
GUNDEM_URL = f"{BASE}/gundem/"
TZ = ZoneInfo("America/New_York")  # Boston saat dilimi

STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"

UA = "ozelkurtdedimi-bot/3.0"

AYLAR_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


def fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def normalize_url(url: str) -> str:
    return (url or "").split("?")[0].strip()


def load_state() -> dict:
    default = {
        "last_daily_date": None,     # "YYYY-MM-DD" (günde 1 tweet için)
        "last_monthly_post": None,   # "YYYY-MM" (ayın 1'inde tek özet)
        "kurt_streak": 0             # Kürt dememe serisi (gün)
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            return default
    return default


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ensure_history_header() -> None:
    if os.path.exists(HISTORY_FILE):
        return
    with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "spoke", "kurt", "url"])


def append_history(d: date, spoke: bool, kurt: bool, url: str | None) -> None:
    ensure_history_header()
    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([d.isoformat(), "Y" if spoke else "N", "Y" if kurt else "N", url or ""])


def previous_month(today: date) -> tuple[str, int, int]:
    y, m = today.year, today.month
    if m == 1:
        return (f"{y-1}-12", y - 1, 12)
    return (f"{y}-{m-1:02d}", y, m - 1)


def monthly_stats(year: int, month: int) -> dict:
    stats = {"days": 0, "spoke_yes": 0, "spoke_no": 0, "kurt_yes": 0, "kurt_no": 0}
    if not os.path.exists(HISTORY_FILE):
        return stats

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                d = datetime.fromisoformat(row["date"]).date()
            except Exception:
                continue
            if d.year == year and d.month == month:
                stats["days"] += 1
                spoke = (row.get("spoke") == "Y")
                kurt = (row.get("kurt") == "Y")
                stats["spoke_yes"] += 1 if spoke else 0
                stats["spoke_no"] += 0 if spoke else 1
                stats["kurt_yes"] += 1 if kurt else 0
                stats["kurt_no"] += 0 if kurt else 1

    return stats


def ensure_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def x_client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=ensure_env("X_API_KEY"),
        consumer_secret=ensure_env("X_API_KEY_SECRET"),
        access_token=ensure_env("X_ACCESS_TOKEN"),
        access_token_secret=ensure_env("X_ACCESS_TOKEN_SECRET"),
        wait_on_rate_limit=True,
    )


def tweet(text: str) -> None:
    x_client().create_tweet(text=text)


def find_latest_ozel_link(gundem_html: str) -> str | None:
    soup = BeautifulSoup(gundem_html, "html.parser")
    # “Özgür Özel” geçen linkleri topla (üsttekiler daha güncel olur)
    candidates: list[str] = []
    for a in soup.select("a[href]"):
        t = (a.get_text(" ", strip=True) or "").lower()
        if ("özgür özel" not in t) and ("ozgur ozel" not in t):
            continue
        href = a.get("href") or ""
        if href.startswith("/"):
            href = BASE + href
        if href.startswith(BASE):
            u = normalize_url(href)
            if u and u not in candidates:
                candidates.append(u)

    return candidates[0] if candidates else None


def extract_article_text(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    ps = [p.get_text(" ", strip=True) for p in soup.select("p") if p.get_text(strip=True)]
    return "\n".join(ps).strip()


def contains_kurt(text: str) -> bool:
    return "kürt" in text.lower()


def make_snippet(text: str, needle: str = "kürt", radius: int = 110) -> str | None:
    low = text.lower()
    idx = low.find(needle)
    if idx == -1:
        return None
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return snippet


def tr_date_str(d: date) -> str:
    return f"{d.day} {AYLAR_TR[d.month]} {d.year}"


def main():
    now = datetime.now(TZ)
    today = now.date()
    today_key = today.isoformat()
    hour = now.hour

    state = load_state()

    # ✅ Ayın 1’inde (09:00+): geçen ay özeti (ayda 1 kez)
    if today.day == 1 and hour >= 9:
        prev_key, py, pm = previous_month(today)
        if state.get("last_monthly_post") != prev_key:
            s = monthly_stats(py, pm)
            if s["days"] > 0:
                msg = (
                    f"📊 {prev_key} özeti\n\n"
                    f"🗣 Konuştuğu gün: {s['spoke_yes']}\n"
                    f"🤐 Konuşmadığı gün: {s['spoke_no']}\n\n"
                    f"🟥 “Kürt” dediği gün: {s['kurt_yes']}\n"
                    f"⬜ “Kürt” demediği gün: {s['kurt_no']}\n\n"
                    f"Kaynak: {GUNDEM_URL}"
                )
                tweet(msg)
                state["last_monthly_post"] = prev_key
                save_state(state)

    # ✅ Günde 1 tweet kuralı
    if state.get("last_daily_date") == today_key:
        return

    # Gündemi kontrol et
    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    spoke = False
    kurt = False
    snippet = None

    if latest_url:
        spoke = True
        article_html = fetch(latest_url)
        text = extract_article_text(article_html)
        kurt = contains_kurt(text)
        if kurt:
            snippet = make_snippet(text, "kürt")

    # ✅ Günde 3 kontrol: sabah/öğlen koşularında “konuşma yoksa” tweet atmayalım.
    # Akşam 17:00+ ise hâlâ yoksa “bulunamadı” tweeti at.
    if (not spoke) and hour < 17:
        return

    # ✅ Streak (X gündür Kürt demiyor)
    streak = int(state.get("kurt_streak", 0))

    if spoke:
        if kurt:
            streak = 0
        else:
            streak += 1
    else:
        # konuşma bulunamadıysa sayaç artmasın (istersen artırırız ama daha “adil” bu)
        pass

    state["kurt_streak"] = streak

    date_str = tr_date_str(today)

    # ✅ İlk satır sabit: “Özgür Özel bugün Kürt dedi mi?”
    if spoke:
        result = "DEDİ" if kurt else "DEMEDİ"

        # ✅ 3 gün olunca viral tweet (ekstra tweet değil; o günkü tweet “viral format”)
        if (not kurt) and streak >= 3:
            msg = (
                "Özgür Özel bugün “Kürt” dedi mi?\n\n"
                f"⬜ SONUÇ: DEMEDİ\n\n"
                f"⏱ {streak} gündür “Kürt” demiyor.\n\n"
                f"📅 {date_str}\n"
                f"🔗 Kaynak:\n{latest_url}"
            )
        else:
            lines = [
                "Özgür Özel bugün “Kürt” dedi mi?",
                "",
                f"🟥 SONUÇ: {result}",
            ]
            if not kurt:
                lines += ["", f"⏱ {streak} gündür “Kürt” demiyor."]
            if snippet:
                s = re.sub(r"\s+", " ", snippet).strip()
                if len(s) > 220:
                    s = s[:217] + "..."
                lines += ["", f"🔎 Alıntı: “{s}”"]
            lines += ["", f"📅 {date_str}", f"🔗 Kaynak:\n{latest_url}"]
            msg = "\n".join(lines)

        tweet(msg)
        append_history(today, spoke=True, kurt=kurt, url=latest_url)

    else:
        msg = (
            "Özgür Özel bugün “Kürt” dedi mi?\n\n"
            "⬜ SONUÇ: Konuşma bulunamadı (CHP Gündem taraması)\n\n"
            f"📅 {date_str}\n"
            f"🔗 Kaynak:\n{GUNDEM_URL}"
        )
        tweet(msg)
        append_history(today, spoke=False, kurt=False, url=None)

    state["last_daily_date"] = today_key
    save_state(state)


if __name__ == "__main__":
    main()
