import os
import json
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import tweepy

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


def tweet_with_reply(main_text: str) -> None:
    client = x_client()
    tw = client.create_tweet(text=main_text)
    tweet_id = tw.data["id"]
    reply = "🔁 Takip etmek için takip edin.\n\n📊 Son 30 gün istatistiği yakında paylaşılacak."
    client.create_tweet(text=reply, in_reply_to_tweet_id=tweet_id)


def tweet_simple(text: str) -> None:
    x_client().create_tweet(text=text)


def detect_slot(now: datetime):
    """
    Schedule: 14:00, 19:00, 23:59 (TRT)
    GitHub cron gecikebildiği için toleranslı çalışıyoruz:
      - 14:00-14:09 => t14
      - 19:00-19:09 => t19
      - 23:50-23:59 veya 00:00-00:09 => t2359 (final)
    Slot dışıysa (None,None,None) döner -> bot hiçbir şey yapmadan çıkar.
    """
    h, m = now.hour, now.minute

    if h == 14 and 0 <= m <= 9:
        return ("t14", "14:00", False)

    if h == 19 and 0 <= m <= 9:
        return ("t19", "19:00", False)

    if (h == 23 and 50 <= m <= 59) or (h == 0 and 0 <= m <= 9):
        return ("t2359", "23:59", True)

    return (None, None, None)


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {
        "daily": {},
        "streak": 0,             # sadece konuştuğu günler içinde: kaç konuşma günüdür “Kürt” demiyor
        "viral_posted_for_streak": {},  # örn: {"3": "2026-03-05"} gibi tekrar atmasın
        "last_monthly_post": ""  # "YYYY-MM"
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

    if not isinstance(data.get("streak"), int):
        try:
            data["streak"] = int(data.get("streak", 0))
        except Exception:
            data["streak"] = 0

    if not isinstance(data.get("viral_posted_for_streak"), dict):
        data["viral_posted_for_streak"] = {}

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
        w.writerow(["date", "spoke", "kurt", "url"])  # spoke: Y/N, kurt: Y/N (kurt sadece spoke=Y iken anlamlı)


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


def monthly_stats_spoken_only(year: int, month: int) -> dict:
    """
    Sadece konuştuğu günler içinde:
      - kurt_yes: kaç konuşma gününde “Kürt” dedi
      - kurt_no: kaç konuşma gününde “Kürt” demedi
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
def find_latest_ozel_link(gundem_html: str) -> str | None:
    """
    Gündem sayfasından "Özgür Özel" geçen ilk linki bulmaya çalışır.
    (CHP sayfası değişirse selectorlar güncellenebilir.)
    """
    soup = BeautifulSoup(gundem_html, "html.parser")
    candidates = []

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


# -------------------------
# Main
# -------------------------
def main():
    print("BOT STARTED:", datetime.now(TZ).isoformat())

    now = datetime.now(TZ)
    today = now.date()
    today_key = today.isoformat()
    date_str = tr_date_str(today)

    slot_key, slot_label, is_final = detect_slot(now)
    print("NOW:", now.isoformat(), "| SLOT:", slot_key, slot_label, "final=", is_final)

    # Slot değilse hiçbir şey yapma
    if slot_key is None:
        print("Not a scheduled slot time. Exiting.")
        return

    state = load_state()
    cleanup_daily(state, keep_days=30)

    daily = state["daily"].get(
        today_key,
        {
            "t14": False,
            "t19": False,
            "t2359": False,
            "spoke_any": False,
            "kurt_any": False,
            "last_url": None
        }
    )

    # Aynı slot daha önce işlendi mi?
    if daily.get(slot_key) is True:
        print("This slot already processed. Exiting.")
        state["daily"][today_key] = daily
        save_state(state)
        return

    # ✅ Aylık istatistik (ayın 1'i, 09:00+ TRT)
    if today.day == 1 and now.hour >= 9:
        prev_key, py, pm = previous_month(today)
        if state.get("last_monthly_post") != prev_key:
            s = monthly_stats_spoken_only(py, pm)
            if s["spoken_days"] > 0:
                msg = (
                    f"📊 {prev_key} özeti (sadece konuştuğu günler)\n\n"
                    f"🗣 Konuştuğu gün sayısı: {s['spoken_days']}\n\n"
                    f"🟥 “Kürt” dediği konuşma günü: {s['kurt_yes']}\n"
                    f"⬜ “Kürt” demediği konuşma günü: {s['kurt_no']}\n\n"
                    f"Kaynak: {GUNDEM_URL}"
                )
                print("MONTHLY TWEET:", msg.replace("\n", " | "))
                tweet_simple(msg)

            state["last_monthly_post"] = prev_key
            save_state(state)

    # Güncel link + “kürt” kontrolü
    print("Fetching:", GUNDEM_URL)
    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)
    print("Latest Ozel URL:", latest_url)

    spoke_now = False
    kurt_now = False

    if latest_url:
        spoke_now = True
        article_html = fetch(latest_url)
        text = extract_article_text(article_html)
        kurt_now = contains_kurt(text)

    # Gün boyunca birikimli durum
    if spoke_now:
        daily["spoke_any"] = True
    if kurt_now:
        daily["kurt_any"] = True
    if latest_url:
        daily["last_url"] = latest_url

    streak = int(state.get("streak", 0))

    print("DECISION SNAPSHOT:", {
        "spoke_now": spoke_now,
        "kurt_now": kurt_now,
        "spoke_any": daily["spoke_any"],
        "kurt_any": daily["kurt_any"],
        "streak_before": streak,
        "is_final": is_final,
        "slot": slot_key
    })

    # =========================
    # ARA TWEET (14:00 / 19:00)
    # =========================
    if not is_final:
        # Gün içinde “Kürt” yakalandıysa ara tweet atma (sadece final)
        if daily["kurt_any"]:
            print("Intermediate: Kurt already found today -> skip intermediate tweet.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        # Ara tweet: sadece konuşma VAR ama “Kürt” YOK ise
        if not daily["spoke_any"]:
            print("Intermediate: No speech yet -> no tweet.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            f"⬜ {slot_label} itibarıyla: HENÜZ DEMEDİ\n\n"
            f"📅 {date_str}"
        )
        print("Posting INTERMEDIATE tweet.")
        tweet_with_reply(main_text)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # =========================
    # FINAL TWEET (23:59)
    # =========================
    if not daily["spoke_any"]:
        # Konuşmadıysa: sayaç/istatistik sayacı güncelleme YOK (streak aynı kalır)
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: Bugün konuşmadı.\n\n"
            f"📅 {date_str}"
        )
        print("Posting FINAL tweet: No speech today.")
        tweet_with_reply(main_text)

        append_history(today, spoke=False, kurt=False, url=None)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # Konuştuysa: sonuç “Kürt dedi mi?”
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
        print("Posting FINAL tweet: SAID KURT. Reset streak.")
        append_history(today, spoke=True, kurt=True, url=daily["last_url"])
    else:
        state["streak"] = streak + 1
        st = state["streak"]
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: DEMEDİ\n\n"
            f"⏱ {st} konuşma günüdür “Kürt” demiyor.\n\n"
            f"📅 {date_str}"
        )
        print("Posting FINAL tweet: DID NOT SAY KURT. New streak:", st)
        append_history(today, spoke=True, kurt=False, url=daily["last_url"])

        # Viral tweet: 3 konuşma günü olunca 1 kez
        # (aynı streak için aynı gün tekrar atmasın diye state içinde işaretliyoruz)
        posted = state.get("viral_posted_for_streak", {})
        if st == 3 and posted.get("3") != today_key:
            viral_text = "⚠️ Özgür Özel 3 konuşma günüdür konuşmalarında “Kürt” demiyor."
            print("Posting VIRAL tweet:", viral_text)
            tweet_simple(viral_text)
            posted["3"] = today_key
            state["viral_posted_for_streak"] = posted

    tweet_with_reply(main_text)

    daily[slot_key] = True
    state["daily"][today_key] = daily
    save_state(state)
    print("BOT FINISHED OK.")


if __name__ == "__main__":
    main()
