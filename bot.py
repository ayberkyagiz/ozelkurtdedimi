import os
import json
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import tweepy
import tempfile
from generate_card import make_card

BASE = "https://chp.org.tr"
GUNDEM_URL = f"{BASE}/gundem/"
TZ = ZoneInfo("Europe/Istanbul")

STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"
UA = "ozelkurtdedimi-bot/10.0"

AYLAR_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


# -------------------------
# Utilities
# -------------------------
def tr_date_str(d: date) -> str:
    return f"{d.day} {AYLAR_TR[d.month]} {d.year}"


def fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def normalize_url(url: str) -> str:
    return (url or "").split("?")[0].strip()


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


def x_api_v1() -> tweepy.API:
    auth = tweepy.OAuth1UserHandler(
        ensure_env("X_API_KEY"),
        ensure_env("X_API_KEY_SECRET"),
        ensure_env("X_ACCESS_TOKEN"),
        ensure_env("X_ACCESS_TOKEN_SECRET"),
    )
    return tweepy.API(auth)


def upload_card(sonuc: str, streak: int, tarih: str) -> Optional[str]:
    """Kart oluştur, Twitter'a yükle, media_id döndür. DEDİ için çağrılmaz."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            card_path = f.name
        make_card(sonuc, streak, tarih, out=card_path)
        api = x_api_v1()
        media = api.media_upload(card_path)
        os.unlink(card_path)
        return str(media.media_id)
    except Exception as e:
        print("Card upload failed (non-fatal):", repr(e))
        return None


def tweet_with_reply(main_text: str, media_id: Optional[str] = None) -> None:
    client = x_client()
    kwargs = {"text": main_text}
    if media_id:
        kwargs["media_ids"] = [media_id]
    tw = client.create_tweet(**kwargs)
    tweet_id = tw.data["id"]
    reply = "🔁 Takip etmek için takip edin.\n\n📊 Son 30 gün istatistiği yakında paylaşılacak."
    try:
        client.create_tweet(text=reply, in_reply_to_tweet_id=tweet_id)
    except Exception as e:
        print("Reply tweet failed (non-fatal):", repr(e))


def tweet_simple(text: str) -> None:
    x_client().create_tweet(text=text)


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {"daily": {}, "streak": 0, "last_monthly_post": ""}
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    for k, v in default.items():
        if k not in data:
            data[k] = v
    if not isinstance(data.get("daily"), dict):
        data["daily"] = {}
    try:
        data["streak"] = int(data.get("streak", 0))
    except Exception:
        data["streak"] = 0
    if not isinstance(data.get("last_monthly_post"), str):
        data["last_monthly_post"] = ""
    return data


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cleanup_daily(state: dict, keep_days: int = 30) -> None:
    if not isinstance(state.get("daily"), dict):
        state["daily"] = {}
        return
    cutoff = (datetime.now(TZ).date() - timedelta(days=keep_days)).isoformat()
    for k in [k for k in state["daily"] if k < cutoff]:
        del state["daily"][k]


# -------------------------
# History / monthly stats
# -------------------------
def ensure_history_header() -> None:
    if os.path.exists(HISTORY_FILE):
        return
    with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(["date", "spoke", "kurt", "url"])


def append_history(d: date, spoke: bool, kurt: bool, url: Optional[str]) -> None:
    ensure_history_header()
    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([
            d.isoformat(), "Y" if spoke else "N", "Y" if kurt else "N", url or ""
        ])


def previous_month(today: date) -> tuple[str, int, int]:
    y, m = today.year, today.month
    if m == 1:
        return (f"{y-1}-12", y - 1, 12)
    return (f"{y}-{m-1:02d}", y, m - 1)


def monthly_stats_spoken_only(year: int, month: int) -> dict:
    stats = {"spoken_days": 0, "kurt_yes": 0, "kurt_no": 0}
    if not os.path.exists(HISTORY_FILE):
        return stats
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(row["date"]).date()
            except Exception:
                continue
            if d.year != year or d.month != month:
                continue
            if row.get("spoke") != "Y":
                continue
            stats["spoken_days"] += 1
            if row.get("kurt") == "Y":
                stats["kurt_yes"] += 1
            else:
                stats["kurt_no"] += 1
    return stats


# -------------------------
# CHP parsing
# -------------------------
def parse_article_date(soup: BeautifulSoup) -> Optional[datetime]:
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        try:
            return datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        except Exception:
            pass
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            dp = data.get("datePublished") or data.get("dateModified")
            if dp:
                return datetime.fromisoformat(dp.replace("Z", "+00:00"))
        except Exception:
            pass
    return None


def find_latest_ozel_link(gundem_html: str) -> Optional[str]:
    soup = BeautifulSoup(gundem_html, "html.parser")
    seen = set()
    candidates = []
    for a in soup.select("a[href]"):
        t = (a.get_text(" ", strip=True) or "").lower()
        if ("özgür özel" not in t) and ("ozgur ozel" not in t):
            continue
        href = a.get("href") or ""
        if href.startswith("/"):
            href = BASE + href
        if not href.startswith(BASE):
            continue
        u = normalize_url(href)
        if u and u not in seen:
            seen.add(u)
            candidates.append(u)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    dated = []
    for url in candidates:
        try:
            dt = parse_article_date(BeautifulSoup(fetch(url), "html.parser"))
        except Exception as e:
            print(f"Date fetch failed for {url}: {repr(e)}")
            dt = None
        dated.append((dt, url))

    dated.sort(key=lambda x: (x[0] is not None, x[0] or datetime.min), reverse=True)
    best_dt, best_url = dated[0]
    print(f"Latest Özel article: {best_url} (date={best_dt})")
    return best_url


def extract_article_text(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    return "\n".join(
        p.get_text(" ", strip=True)
        for p in soup.select("p")
        if p.get_text(strip=True)
    ).strip()


def contains_kurt(text: str) -> bool:
    return "kürt" in (text or "").lower()


# -------------------------
# Main
# -------------------------
def main():
    print("BOT STARTED:", datetime.now().isoformat())

    now = datetime.now(TZ)

    # Backfill: FORCE_DATE=YYYY-MM-DD
    override = os.getenv("FORCE_DATE", "").strip()
    if override:
        try:
            today = date.fromisoformat(override)
        except Exception:
            raise RuntimeError("FORCE_DATE must be YYYY-MM-DD")
    else:
        today = now.date()

    today_key = today.isoformat()
    date_str = tr_date_str(today)

    print(f"NOW: {now.isoformat()} | TODAY_KEY: {today_key}")

    state = load_state()
    cleanup_daily(state, keep_days=30)

    daily = state["daily"].get(today_key, {"done": False, "spoke_any": False, "kurt_any": False, "last_url": None})

    # Bugün zaten işlendiyse çık
    if daily.get("done") is True:
        print("Today already processed. Exiting.")
        state["daily"][today_key] = daily
        save_state(state)
        return

    # -------------------------
    # Aylık istatistik (ayın 1'i): backfill koşusunda atlanır
    # -------------------------
    if (not override) and today.day == 1:
        prev_key, py, pm = previous_month(today)
        if state.get("last_monthly_post") != prev_key:
            s = monthly_stats_spoken_only(py, pm)
            if s["spoken_days"] > 0:
                msg = (
                    f"📊 {prev_key} özeti (sadece konuştuğu günler)\n\n"
                    f"🗣 Konuştuğu gün sayısı: {s['spoken_days']}\n\n"
                    f'🟥 "Kürt" dediği konuşma günü: {s["kurt_yes"]}\n'
                    f'⬜ "Kürt" demediği konuşma günü: {s["kurt_no"]}\n\n'
                    f"Kaynak: {GUNDEM_URL}"
                )
                state["last_monthly_post"] = prev_key
                save_state(state)
                print("Posting monthly stats…")
                tweet_simple(msg)

    # -------------------------
    # CHP kontrol
    # -------------------------
    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    spoke_now = False
    kurt_now = False

    if latest_url:
        spoke_now = True
        try:
            text = extract_article_text(fetch(latest_url))
            kurt_now = contains_kurt(text)
        except Exception as e:
            print("Article fetch/parse error:", repr(e))

    print(f"Detected: spoke_now={spoke_now}, kurt_now={kurt_now}, url={latest_url}")

    streak = int(state.get("streak", 0))

    # -------------------------
    # Tweet
    # -------------------------
    if not spoke_now:
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: Bugün konuşmadı.\n\n"
            f"📅 {date_str}"
        )
        print("Posting (no speech) tweet…")
        media_id = upload_card("konusmadi", 0, date_str)
        tweet_with_reply(main_text, media_id=media_id)
        append_history(today, spoke=False, kurt=False, url=None)

    elif kurt_now:
        state["streak"] = 0
        src = latest_url or GUNDEM_URL
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "🟥 SONUÇ: DEDİ\n\n"
            "⏱ Sayaç sıfırlandı.\n\n"
            f"📅 {date_str}\n\n"
            f"🔗 Kaynak:\n{src}"
        )
        print("Posting (kurt said) tweet…")
        tweet_with_reply(main_text)  # DEDİ: kart yok, link var
        append_history(today, spoke=True, kurt=True, url=latest_url)

    else:
        state["streak"] = streak + 1
        st = state["streak"]
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: DEMEDİ\n\n"
            f'⏱ {st} konuşma günüdür "Kürt" demiyor.\n\n'
            f"📅 {date_str}"
        )
        print("Posting (kurt NOT said) tweet…")
        media_id = upload_card("demedi", st, date_str)
        tweet_with_reply(main_text, media_id=media_id)
        append_history(today, spoke=True, kurt=False, url=latest_url)

        if st == 3:
            print("Posting streak=3 alert…")
            tweet_simple('⚠️ Özgür Özel 3 konuşma günüdür konuşmalarında "Kürt" demiyor.')

    daily["done"] = True
    daily["spoke_any"] = spoke_now
    daily["kurt_any"] = kurt_now
    daily["last_url"] = latest_url
    state["daily"][today_key] = daily
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
