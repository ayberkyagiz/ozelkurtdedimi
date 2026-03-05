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
    """Kart oluştur, Twitter'a yükle, media_id döndür."""
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


def detect_slot(now: datetime) -> tuple[str, str, bool]:
    """
    Cron job dakika kayması yaşayabilir; ±2 dakika tolerans eklendi.
    Schedule: 14:00, 19:00, 23:59 TRT

    FORCE_SLOT env var ile override edilebilir (backfill için).
    Geçerli değerler: t14, t19, t2359
    """
    force = os.getenv("FORCE_SLOT", "").strip().lower()
    if force == "t14":
        return ("t14", "14:00", False)
    if force == "t19":
        return ("t19", "19:00", False)
    if force == "t2359":
        return ("t2359", "23:59", True)

    h, m = now.hour, now.minute

    if h == 14 and m <= 2:
        return ("t14", "14:00", False)
    if h == 19 and m <= 2:
        return ("t19", "19:00", False)
    # 23:57-23:59 aralığını da yakala
    if h == 23 and m >= 57:
        return ("t2359", "23:59", True)

    # Beklenmedik saatte çalıştıysa (manuel tetik vs.) final slot'u döndür
    return ("t2359", "23:59", True)


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {
        "daily": {},
        # "streak" = SADECE konuştuğu günler içinde: kaç konuşma günüdür "Kürt" demiyor
        "streak": 0,
        # "YYYY-MM"
        "last_monthly_post": ""
    }
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
    if "daily" not in state or not isinstance(state["daily"], dict):
        state["daily"] = {}
        return
    cutoff = (datetime.now(TZ).date() - timedelta(days=keep_days)).isoformat()
    to_delete = [k for k in state["daily"].keys() if k < cutoff]
    for k in to_delete:
        del state["daily"][k]


# -------------------------
# History for monthly stats
# -------------------------
def ensure_history_header() -> None:
    if os.path.exists(HISTORY_FILE):
        return
    with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "spoke", "kurt", "url"])  # spoke: Y/N, kurt: Y/N


def append_history(d: date, spoke: bool, kurt: bool, url: Optional[str]) -> None:
    ensure_history_header()
    with open(HISTORY_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([d.isoformat(), "Y" if spoke else "N", "Y" if kurt else "N", url or ""])


def previous_month(today: date) -> tuple[str, int, int]:
    y, m = today.year, today.month
    if m == 1:
        return (f"{y-1}-12", y - 1, 12)
    return (f"{y}-{m-1:02d}", y, m - 1)


def monthly_stats_spoken_only(year: int, month: int) -> dict:
    """
    SADECE konuştuğu günler içinde:
    - kurt_yes: kaç konuşma gününde "Kürt" dedi
    - kurt_no: kaç konuşma gününde "Kürt" demedi
    """
    stats = {"spoken_days": 0, "kurt_yes": 0, "kurt_no": 0}
    if not os.path.exists(HISTORY_FILE):
        return stats

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                d = datetime.fromisoformat(row["date"]).date()
            except Exception:
                continue
            if d.year != year or d.month != month:
                continue

            spoke = (row.get("spoke") == "Y")
            if not spoke:
                continue

            stats["spoken_days"] += 1
            kurt = (row.get("kurt") == "Y")
            if kurt:
                stats["kurt_yes"] += 1
            else:
                stats["kurt_no"] += 1

    return stats


# -------------------------
# CHP parsing
# -------------------------
def parse_article_date(soup: BeautifulSoup) -> Optional[datetime]:
    """
    CHP makale sayfasındaki tarih bilgisini çeker.
    <time datetime="..."> veya yaygın CSS class'larını dener.
    Bulamazsa None döner.
    """
    # 1) <time datetime="..."> etiketi
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        try:
            return datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        except Exception:
            pass

    # 2) JSON-LD içindeki datePublished
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
    """
    Özgür Özel'e ait en YENİ haberin URL'ini döndürür.

    Strateji:
    1. Gündem sayfasındaki tüm 'Özgür Özel' linklerini topla.
    2. Her linkin makale sayfasını çekip tarihini oku.
    3. En yeni tarihe sahip olanı döndür.
    4. Tarih okunamazsa DOM sırasındaki ilk link fallback olarak kullanılır.
    """
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

    # Birden fazla aday varsa tarihe göre sırala
    dated: list[tuple[Optional[datetime], str]] = []
    for url in candidates:
        try:
            html = fetch(url)
            article_soup = BeautifulSoup(html, "html.parser")
            dt = parse_article_date(article_soup)
        except Exception as e:
            print(f"Date fetch failed for {url}: {repr(e)}")
            dt = None
        dated.append((dt, url))

    # Tarihi olanları en yeniden eskiye sırala; tarihi olmayanlar sona düşer
    dated.sort(key=lambda x: (x[0] is not None, x[0] or datetime.min), reverse=True)

    best_dt, best_url = dated[0]
    print(f"Latest Özel article: {best_url} (date={best_dt})")
    return best_url


def extract_article_text(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    ps = [p.get_text(" ", strip=True) for p in soup.select("p") if p.get_text(strip=True)]
    return "\n".join(ps).strip()


def contains_kurt(text: str) -> bool:
    return "kürt" in (text or "").lower()


# -------------------------
# Main
# -------------------------
def main():
    print("BOT STARTED:", datetime.now().isoformat())

    now = datetime.now(TZ)

    # Backfill override: FORCE_DATE=YYYY-MM-DD
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

    slot_key, slot_label, is_final = detect_slot(now)
    print(f"NOW: {now.isoformat()} | SLOT: {slot_key} {slot_label} final={is_final} | TODAY_KEY: {today_key}")

    state = load_state()
    cleanup_daily(state, keep_days=30)

    daily = state["daily"].get(
        today_key,
        {"t14": False, "t19": False, "t2359": False, "spoke_any": False, "kurt_any": False, "last_url": None}
    )

    # Aynı slot tekrar çalıştıysa çık
    if daily.get(slot_key) is True:
        print("This slot already processed. Exiting.")
        state["daily"][today_key] = daily
        save_state(state)
        return

    # -------------------------
    # Aylık istatistik (ayın 1'i, 09:00+): sadece konuştuğu günler içinde
    # Not: Backfill koşusunda monthly spam olmasın diye sadece override yoksa çalıştırıyoruz.
    # Race condition fix: state'i tweet'ten ÖNCE kaydet
    # -------------------------
    if (not override) and today.day == 1 and now.hour >= 9:
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
                # Önce state'i kaydet, sonra tweet at (crash koruması)
                state["last_monthly_post"] = prev_key
                save_state(state)
                print("Posting monthly stats…")
                tweet_simple(msg)

    # -------------------------
    # CHP kontrol
    # -------------------------
    spoke_now = False
    kurt_now = False
    latest_url = None

    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    if latest_url:
        spoke_now = True
        try:
            article_html = fetch(latest_url)
            text = extract_article_text(article_html)
            kurt_now = contains_kurt(text)
        except Exception as e:
            print("Article fetch/parse error:", repr(e))
            spoke_now = True
            kurt_now = False

    print(f"Detected: spoke_now={spoke_now}, kurt_now={kurt_now}, url={latest_url}")

    if spoke_now:
        daily["spoke_any"] = True
    if kurt_now:
        daily["kurt_any"] = True
    if latest_url:
        daily["last_url"] = latest_url

    streak = int(state.get("streak", 0))

    # =========================
    # ARA TWEET (14:00 / 19:00)
    # =========================
    if not is_final:
        if daily["kurt_any"]:
            print("Kurt already detected earlier today; skipping interim tweet.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        if not daily["spoke_any"]:
            print("No speech detected yet; skipping interim tweet.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        if streak < 2:
            print("Streak < 2; skipping interim tweet to reduce spam.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            f"⬜ {slot_label} itibarıyla: HENÜZ DEMEDİ\n\n"
            f'⏱ {streak} konuşma günüdür "Kürt" demiyor.\n\n'
            f"📅 {date_str}"
        )

        print("Posting interim tweet…")
        tweet_with_reply(main_text)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # =========================
    # FINAL TWEET (23:59)
    # =========================
    if not daily["spoke_any"]:
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: Bugün konuşmadı.\n\n"
            f"📅 {date_str}"
        )
        print("Posting final (no speech) tweet…")
        media_id = upload_card("konusmadi", 0, date_str)
        tweet_with_reply(main_text, media_id=media_id)

        append_history(today, spoke=False, kurt=False, url=None)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    if daily["kurt_any"]:
        state["streak"] = 0
        src = daily["last_url"] or GUNDEM_URL
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "🟥 SONUÇ: DEDİ\n\n"
            "⏱ Sayaç sıfırlandı.\n\n"
            f"📅 {date_str}\n\n"
            f"🔗 Kaynak:\n{src}"
        )
        append_history(today, spoke=True, kurt=True, url=daily["last_url"])
        print("Posting final (kurt said) tweet…")
        tweet_with_reply(main_text)  # DEDİ: link var, kart yok

    else:
        state["streak"] = streak + 1
        st = state["streak"]

        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: DEMEDİ\n\n"
            f'⏱ {st} konuşma günüdür "Kürt" demiyor.\n\n'
            f"📅 {date_str}"
        )

        append_history(today, spoke=True, kurt=False, url=daily["last_url"])

        print("Posting final (kurt NOT said) tweet…")
        media_id = upload_card("demedi", st, date_str)
        tweet_with_reply(main_text, media_id=media_id)

        if st == 3:
            print("Posting viral streak=3 tweet…")
            tweet_simple("\u26a0\ufe0f Özgür Özel 3 konuşma günüdür konuşmalarında \"Kürt\" demiyor.")

    daily[slot_key] = True
    state["daily"][today_key] = daily
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
