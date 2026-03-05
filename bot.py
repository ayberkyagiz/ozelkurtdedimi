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


def detect_slot(now: datetime) -> tuple[str, str, bool]:
    # schedule: 14:00, 19:00, 23:59 TRT
    if now.hour == 14 and now.minute == 0:
        return ("t14", "14:00", False)
    if now.hour == 19 and now.minute == 0:
        return ("t19", "19:00", False)
    return ("t2359", "23:59", True)


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {
        "daily": {},             # {"YYYY-MM-DD": {"t14":bool,"t19":bool,"t2359":bool,"spoke_any":bool,"kurt_any":bool,"last_url":str|None,"tweeted_today":bool}}
        "streak": 0,             # SADECE konuştuğu günler içinde: kaç konuşma günüdür “Kürt” demiyor
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
        # spoke: Y/N, kurt: Y/N (kurt sadece spoke=Y iken anlamlı)
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


def monthly_stats_spoken_only(year: int, month: int) -> dict:
    """
    SADECE konuştuğu günler içinde:
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
    return "kürt" in (text or "").lower()


# -------------------------
# Main
# -------------------------
def main():
    now = datetime.now(TZ)
    print("BOT STARTED:", now.isoformat())

    today = now.date()
    today_key = today.isoformat()
    date_str = tr_date_str(today)

    slot_key, slot_label, is_final = detect_slot(now)
    print(f"Slot: {slot_key} ({slot_label}), final={is_final}")

    state = load_state()
    cleanup_daily(state, keep_days=30)

    # Daily record
    daily = state["daily"].get(
        today_key,
        {
            "t14": False,
            "t19": False,
            "t2359": False,
            "spoke_any": False,
            "kurt_any": False,
            "last_url": None,
            "tweeted_today": False,
        },
    )

    # Prevent duplicate runs for the same slot
    if daily.get(slot_key) is True:
        print("This slot already processed. Exiting.")
        state["daily"][today_key] = daily
        save_state(state)
        return

    # ✅ Monthly stats tweet (Day 1, after 09:00 TRT) — spoken-days only
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
                print("Posting monthly stats tweet:", prev_key)
                tweet_simple(msg)
                state["last_monthly_post"] = prev_key
                save_state(state)
            else:
                print("Monthly stats: no spoken days last month, skipping tweet.")

    # Fetch + detect latest speech
    print("Fetching CHP Gündem page...")
    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    spoke_now = False
    kurt_now = False

    if latest_url:
        spoke_now = True
        print("Speech detected:", latest_url)
        article_html = fetch(latest_url)
        text = extract_article_text(article_html)
        kurt_now = contains_kurt(text)
        print("Keyword check:", "FOUND" if kurt_now else "NOT FOUND")
    else:
        print("No Özgür Özel speech link found on Gündem.")

    # Update daily aggregated flags
    if spoke_now:
        daily["spoke_any"] = True
    if kurt_now:
        daily["kurt_any"] = True
    if latest_url:
        daily["last_url"] = latest_url

    streak = int(state.get("streak", 0))
    print("Current streak (spoken-days only):", streak)
    print("Daily state so far:", {"spoke_any": daily["spoke_any"], "kurt_any": daily["kurt_any"], "tweeted_today": daily["tweeted_today"]})

    # =========================
    # NO-TWEET GUARD: 1 tweet/day max
    # =========================
    if daily.get("tweeted_today"):
        print("Already tweeted today. Marking slot done and exiting.")
        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # =========================
    # INTERMEDIATE (14:00 / 19:00)
    # =========================
    if not is_final:
        # If we already caught "Kürt" today, no intermediate tweet; final will be enough.
        if daily["kurt_any"]:
            print("Kürt already found earlier today -> skip intermediate.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        # IMPORTANT: You said the key is "spoke but didn't say Kürt".
        # If there is NO speech yet, we do NOT tweet.
        if not daily["spoke_any"]:
            print("No speech yet -> no intermediate tweet.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        # Anti-spam: intermediate tweet only if streak >= 2 (i.e., already 2 spoken-days without Kürt)
        if streak < 2:
            print("Streak < 2 -> skipping intermediate tweet to reduce spam.")
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            f"⬜ {slot_label} itibarıyla: HENÜZ DEMEDİ\n\n"
            f"⏱ {streak} konuşma günüdür “Kürt” demiyor.\n\n"
            f"📅 {date_str}"
        )
        print("Posting intermediate tweet.")
        tweet_with_reply(main_text)

        daily["tweeted_today"] = True
        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        print("BOT FINISHED (intermediate).")
        return

    # =========================
    # FINAL (23:59)
    # =========================
    # If no speech happened today, we DO NOT tweet (as requested).
    if not daily["spoke_any"]:
        print("FINAL: No speech today -> no tweet.")
        append_history(today, spoke=False, kurt=False, url=None)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        print("BOT FINISHED (final, no speech).")
        return

    # Speech happened: decide final result
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
        print("FINAL decision: DEDİ (streak reset).")
    else:
        state["streak"] = streak + 1
        st = state["streak"]
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: DEMEDİ\n\n"
            f"⏱ {st} konuşma günüdür “Kürt” demiyor.\n\n"
            f"📅 {date_str}"
        )
        append_history(today, spoke=True, kurt=False, url=daily["last_url"])
        print("FINAL decision: DEMEDİ. New streak:", st)

        # Viral alarms (only on DEMEDİ days, because that’s the streak logic)
        if st == 5:
            print("Posting viral alarm (5).")
            tweet_simple("⚠️ Özgür Özel 5 konuşma günüdür konuşmalarında 'Kürt' demiyor.")
        if st == 10:
            print("Posting viral alarm (10).")
            tweet_simple("⚠️ Özgür Özel 10 konuşma günüdür konuşmalarında 'Kürt' demiyor.")

    print("Posting final tweet.")
    tweet_with_reply(main_text)

    daily["tweeted_today"] = True
    daily[slot_key] = True
    state["daily"][today_key] = daily
    save_state(state)
    print("BOT FINISHED (final).")


if __name__ == "__main__":
    main()
