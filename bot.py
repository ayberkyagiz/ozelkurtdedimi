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
TZ = ZoneInfo("America/New_York")  # Boston/NY time

STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"

UA = "ozelkurtdedimi-bot/2.0"


def fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_daily_date": None,        # "YYYY-MM-DD"
        "last_url": None,               # last tweeted article URL
        "last_monthly_post": None       # "YYYY-MM"
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def normalize_url(url: str) -> str:
    return (url or "").split("?")[0].strip()


def find_latest_ozel_link(gundem_html: str) -> str | None:
    soup = BeautifulSoup(gundem_html, "html.parser")
    candidates = []

    for a in soup.select("a[href]"):
        txt = (a.get_text(" ", strip=True) or "").lower()
        if "özgür özel" not in txt and "ozgur ozel" not in txt:
            continue

        href = a.get("href") or ""
        if href.startswith("/"):
            href = BASE + href
        if href.startswith(BASE):
            candidates.append(normalize_url(href))

    # İlk görünen en güncel olma eğiliminde
    for u in candidates:
        if u:
            return u
    return None


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
    client = x_client()
    client.create_tweet(text=text)


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


def previous_month_key(today: date) -> tuple[str, int, int]:
    # returns ("YYYY-MM", year, month) for previous month
    y, m = today.year, today.month
    if m == 1:
        return (f"{y-1}-12", y - 1, 12)
    return (f"{y}-{m-1:02d}", y, m - 1)


def monthly_stats(year: int, month: int) -> dict:
    # counts from history.csv
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


def main():
    now = datetime.now(TZ)
    today = now.date()
    today_key = today.isoformat()
    hour = now.hour  # local hour

    state = load_state()

    # 1) Ayın 1'inde: geçen ay özeti (bir kez)
    if today.day == 1:
        prev_key, py, pm = previous_month_key(today)
        if state.get("last_monthly_post") != prev_key and hour >= 9:
            s = monthly_stats(py, pm)
            # Eğer hiç veri yoksa spam atma
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

    # 2) Aynı gün zaten tweet atıldıysa çık
    if state.get("last_daily_date") == today_key:
        return

    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    spoke = False
    kurt = False
    snippet = None

    if latest_url:
        spoke = True
        article_html = fetch(latest_url)
        article_text = extract_article_text(article_html)

        kurt = contains_kurt(article_text)
        if kurt:
            snippet = make_snippet(article_text, "kürt")

    # 3) Sabah/öğlen koşularında: eğer konuşma yoksa bekle.
    # Akşam 17:00+ ise, konuşma yoksa da "bulunamadı" tweeti at.
    if not spoke and hour < 17:
        return

    # 4) Viral format: tek tweet
    aylar = {     1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",     7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık" }  date_str = f"{today.day} {aylar[today.month]} {today.year}"

    if spoke:
        result = "DEDİ" if kurt else "DEMEDİ"
        msg_lines = [
            f"📅 {date_str}",
            "",
            "Bugün Özgür Özel “Kürt” dedi mi?",
            f"🟥 SONUÇ: {result}",
        ]
        if snippet:
            # Tweeti şişirmeyelim
            snippet = re.sub(r"\s+", " ", snippet).strip()
            if len(snippet) > 220:
                snippet = snippet[:217] + "..."
            msg_lines += ["", f"🔎 Alıntı: “{snippet}”"]
        msg_lines += ["", f"🔗 Kaynak: {latest_url}"]
        tweet("\n".join(msg_lines))
        append_history(today, spoke=True, kurt=kurt, url=latest_url)
        state["last_url"] = latest_url
    else:
        msg = (
            f"📅 {date_str}\n\n"
            "Bugün Özgür Özel “Kürt” dedi mi?\n"
            "⬜ SONUÇ: Konuşma bulunamadı (CHP Gündem taraması)\n\n"
            f"🔗 Kaynak: {GUNDEM_URL}"
        )
        tweet(msg)
        append_history(today, spoke=False, kurt=False, url=None)

    state["last_daily_date"] = today_key
    save_state(state)


if __name__ == "__main__":
    main()
