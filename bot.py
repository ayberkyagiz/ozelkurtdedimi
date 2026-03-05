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


def tweet_with_reply(main_text: str) -> str:
    """
    Posts main tweet + auto reply.
    Returns the main tweet id.
    """
    client = x_client()
    tw = client.create_tweet(text=main_text)
    tweet_id = str(tw.data["id"])

    reply = "🔁 Takip etmek için takip edin.\n\n📊 Son 30 gün istatistiği yakında paylaşılacak."
    client.create_tweet(text=reply, in_reply_to_tweet_id=tweet_id)

    return tweet_id


def tweet_simple(text: str) -> str:
    tw = x_client().create_tweet(text=text)
    return str(tw.data["id"])


def detect_slot(now: datetime):
    """
    Schedule: 14:00, 19:00, 23:59 (TRT)

    Tolerans:
      - 14:00-14:09 => t14
      - 19:00-19:09 => t19
      - 23:59 exact => t2359 (final)

    Slot dışıysa (None,None,None) döner ve bot hiçbir şey yapmadan çıkar.
    """
    h, m = now.hour, now.minute

    if h == 14 and 0 <= m <= 9:
        return ("t14", "14:00", False)

    if h == 19 and 0 <= m <= 9:
        return ("t19", "19:00", False)

    if h == 23 and m == 59:
        return ("t2359", "23:59", True)

    return (None, None, None)


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {
        "daily": {},             # {"YYYY-MM-DD": {"t14":bool,"t19":bool,"t2359":bool,"tweeted":bool,"spoke_any":bool,"kurt_any":bool,"last_url":str|None,"last_tweet_id":str|None}}
        "streak": 0,             # SADECE konuştuğu günler içinde: kaç konuşma günüdür “Kürt” demiyor
        "last_monthly_post": "", # "YYYY-MM"
        "last_viral_for_streak": 0,  # viral tweet'i aynı streak için 1 kez atmak için
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

    try:
        data["last_viral_for_streak"] = int(data.get("last_viral_for_streak", 0))
    except Exception:
        data["last_viral_for_streak"] = 0

    return data


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cleanup_daily(state: dict, keep_days: int = 30) -> None:
    if "daily" not in state or not isinstance(state["daily"], dict):
        state["daily"] = {}
        return
    cutoff = (datetime.now(TZ).date() - timedelta(days=keep_days)).isoformat()
    to_delete = [k for k in list(state["daily"].keys()) if k < cutoff]
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
    """
    Finds the latest link whose anchor text contains "Özgür Özel" / "Ozgur Ozel".
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
    return "kürt" in (text or "").lower()


# -------------------------
# Main
# -------------------------
def main():
    print("BOT STARTED:", datetime.now().isoformat())

    now = datetime.now(TZ)
    slot_key, slot_label, is_final = detect_slot(now)

    if slot_key is None:
        print("Not a scheduled slot time. Exiting.")
        return

    # IMPORTANT: We intentionally use "today = now.date()".
    # (No after-midnight fallback) to avoid wrong-day tweets.
    today = now.date()
    today_key = today.isoformat()
    date_str = tr_date_str(today)

    print(f"NOW: {now.isoformat()} | SLOT: {slot_key} {slot_label} final={is_final} | REPORT_DAY: {today_key}")

    state = load_state()
    cleanup_daily(state, keep_days=30)

    # Daily state for today
    daily = state["daily"].get(
        today_key,
        {
            "t14": False,
            "t19": False,
            "t2359": False,
            "tweeted": False,      # ✅ ensures max 1 main tweet per day
            "spoke_any": False,
            "kurt_any": False,
            "last_url": None,
            "last_tweet_id": None,
        }
    )

    # If this slot already processed, exit
    if daily.get(slot_key) is True:
        print("This slot already processed. Exiting.")
        state["daily"][today_key] = daily
        save_state(state)
        return

    # Monthly stats (1st day of month, after 09:00 TRT)
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
                tweet_simple(msg)
                state["last_monthly_post"] = prev_key
                save_state(state)
                print("Monthly stats tweeted.")

    # Fetch latest Ozel link + check for "kürt"
    latest_url = None
    spoke_now = False
    kurt_now = False

    try:
        gundem_html = fetch(GUNDEM_URL)
        latest_url = find_latest_ozel_link(gundem_html)
    except Exception as e:
        print("ERROR fetching gundem:", repr(e))
        latest_url = None

    if latest_url:
        spoke_now = True
        try:
            article_html = fetch(latest_url)
            text = extract_article_text(article_html)
            kurt_now = contains_kurt(text)
        except Exception as e:
            print("ERROR fetching article:", repr(e))
            # If article fetch fails, treat as "spoke but unknown content"
            # We'll keep spoke_now True but kurt_now False to avoid false positives.
            kurt_now = False

    # Update accumulators
    if spoke_now:
        daily["spoke_any"] = True
    if kurt_now:
        daily["kurt_any"] = True
    if latest_url:
        daily["last_url"] = latest_url

    streak = int(state.get("streak", 0))
    viral_already_for = int(state.get("last_viral_for_streak", 0))

    # -------------------------
    # Tweeting rules:
    #
    # - Max 1 main tweet per day (daily["tweeted"])
    #
    # - Priority:
    #   1) If final slot: always tweet summary (spoke/no-spoke + kurt result)
    #   2) If not final:
    #        - If "spoke_any" AND NOT "kurt_any" AND NOT tweeted yet -> tweet "Henüz demedi" (only once)
    #        - Otherwise no tweet
    #
    # - If later slot finds "kurt_any" True (dedi), we DO NOT tweet again until final.
    # -------------------------

    # Intermediate slots (14:00 / 19:00)
    if not is_final:
        if daily["tweeted"]:
            print("Already tweeted today. Skipping intermediate tweet.")
        else:
            # We only care when he spoke but didn't say "kürt"
            if daily["spoke_any"] and (not daily["kurt_any"]):
                main_text = (
                    "Özgür Özel bugün Kürt dedi mi?\n\n"
                    f"⬜ {slot_label} itibarıyla: HENÜZ DEMEDİ\n\n"
                    f"📅 {date_str}"
                )
                tweet_id = tweet_with_reply(main_text)
                daily["tweeted"] = True
                daily["last_tweet_id"] = tweet_id
                print("Intermediate tweet posted:", tweet_id)
            else:
                print("No tweet condition met at intermediate slot.")

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # Final slot (23:59) => always tweet daily summary
    if daily["tweeted"]:
        print("Main tweet already posted earlier today, but final still allowed? -> No, keeping 1 tweet/day policy.")
        # We stick to 1 tweet/day: if already tweeted earlier, do NOT tweet final again.
        # But we SHOULD still update streak/history at final.
        pass

    # Determine final outcome
    if not daily["spoke_any"]:
        # Spoke=No -> Tweet "Bugün konuşmadı" (if not already tweeted)
        if not daily["tweeted"]:
            main_text = (
                "Özgür Özel bugün Kürt dedi mi?\n\n"
                "⬜ SONUÇ: Bugün konuşmadı.\n\n"
                f"📅 {date_str}"
            )
            tweet_id = tweet_with_reply(main_text)
            daily["tweeted"] = True
            daily["last_tweet_id"] = tweet_id
            print("Final 'did not speak' tweet posted:", tweet_id)
        else:
            print("Skipping final tweet because 1 tweet/day already posted.")

        # History: spoke=N (monthly stats ignore for counts)
        append_history(today, spoke=False, kurt=False, url=None)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # Spoke=Yes
    if daily["kurt_any"]:
        # He said "Kürt" at least once today
        state["streak"] = 0
        state["last_viral_for_streak"] = 0  # reset viral marker
        if not daily["tweeted"]:
            src = daily["last_url"] or GUNDEM_URL
            main_text = (
                "Özgür Özel bugün Kürt dedi mi?\n\n"
                "🟥 SONUÇ: DEDİ\n\n"
                "⏱ Sayaç sıfırlandı.\n\n"
                f"📅 {date_str}\n\n"
                f"🔗 Kaynak:\n{src}"
            )
            tweet_id = tweet_with_reply(main_text)
            daily["tweeted"] = True
            daily["last_tweet_id"] = tweet_id
            print("Final 'DEDİ' tweet posted:", tweet_id)
        else:
            print("Skipping final tweet because 1 tweet/day already posted.")

        append_history(today, spoke=True, kurt=True, url=daily["last_url"])

    else:
        # He spoke but did NOT say "Kürt"
        state["streak"] = streak + 1
        st = state["streak"]

        if not daily["tweeted"]:
            main_text = (
                "Özgür Özel bugün Kürt dedi mi?\n\n"
                "⬜ SONUÇ: DEMEDİ\n\n"
                f"⏱ {st} konuşma günüdür “Kürt” demiyor.\n\n"
                f"📅 {date_str}"
            )
            tweet_id = tweet_with_reply(main_text)
            daily["tweeted"] = True
            daily["last_tweet_id"] = tweet_id
            print("Final 'DEMEDİ' tweet posted:", tweet_id)
        else:
            print("Skipping final tweet because 1 tweet/day already posted.")

        append_history(today, spoke=True, kurt=False, url=daily["last_url"])

        # Viral tweet at st == 3, only once for that streak value
        if st >= 3 and viral_already_for < 3:
            tweet_simple("⚠️ Özgür Özel 3 konuşma günüdür konuşmalarında “Kürt” demiyor.")
            state["last_viral_for_streak"] = 3
            print("Viral tweet posted for streak=3.")

    daily[slot_key] = True
    state["daily"][today_key] = daily
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
