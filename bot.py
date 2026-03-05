import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from zoneinfo import ZoneInfo
import tweepy

BASE = "https://chp.org.tr"
GUNDEM_URL = f"{BASE}/gundem/"
TZ = ZoneInfo("Europe/Istanbul")

STATE_FILE = "state.json"
UA = "ozelkurtdedimi-bot/7.0"

AYLAR_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


def tr_date_str(d: date) -> str:
    return f"{d.day} {AYLAR_TR[d.month]} {d.year}"


def fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def normalize_url(url: str) -> str:
    return (url or "").split("?")[0].strip()


def load_state() -> dict:
    default = {
        "daily": {},   # { "YYYY-MM-DD": {"t14": bool, "t19": bool, "t2359": bool, "kurt_any": bool, "last_url": str|null } }
        "streak": 0    # kaç gündür "Kürt" demiyor (final sonuçlarına göre)
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default

    if "daily" not in data:
        data["daily"] = {}
    if "streak" not in data:
        data["streak"] = 0
    return data


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    return "kürt" in text.lower()


def detect_slot(now: datetime) -> tuple[str, str, bool]:
    if now.hour == 14 and now.minute == 0:
        return ("t14", "14:00", False)
    if now.hour == 19 and now.minute == 0:
        return ("t19", "19:00", False)
    return ("t2359", "23:59", True)


def main():
    now = datetime.now(TZ)
    today = now.date()
    today_key = today.isoformat()
    date_str = tr_date_str(today)

    slot_key, slot_label, is_final = detect_slot(now)

    state = load_state()
    streak = int(state.get("streak", 0))

    daily = state["daily"].get(
        today_key,
        {"t14": False, "t19": False, "t2359": False, "kurt_any": False, "last_url": None}
    )

    # Aynı slot daha önce işlendi mi?
    if daily.get(slot_key) is True:
        return

    # CHP gündemden güncel ÖÖ linkini bul ve şu an "kürt" var mı bak
    gundem_html = fetch(GUNDEM_URL)
    latest_url = find_latest_ozel_link(gundem_html)

    kurt_now = False
    if latest_url:
        article_html = fetch(latest_url)
        text = extract_article_text(article_html)
        kurt_now = contains_kurt(text)

    if kurt_now:
        daily["kurt_any"] = True
    if latest_url:
        daily["last_url"] = latest_url

    # =========================
    # ARA TWEET (14:00 / 19:00)
    # =========================
    if not is_final:
        # Gün içinde "kurt" yakalandıysa ara tweet yok -> sadece final
        if daily["kurt_any"]:
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        # ✅ Spam azaltma: streak < 2 ise ara tweet atma
        if streak < 2:
            daily[slot_key] = True
            state["daily"][today_key] = daily
            save_state(state)
            return

        # streak >= 2 ise "henüz demedi" tweeti at
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            f"⬜ {slot_label} itibarıyla: HENÜZ DEMEDİ\n\n"
            f"⏱ {streak} gündür “Kürt” demiyor.\n\n"
            f"📅 {date_str}"
        )
        tweet_with_reply(main_text)

        daily[slot_key] = True
        state["daily"][today_key] = daily
        save_state(state)
        return

    # =========================
    # FINAL TWEET (23:59)
    # =========================
    if daily["kurt_any"]:
        # dedi -> streak sıfır
        state["streak"] = 0
        src = daily["last_url"] or GUNDEM_URL
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "🟥 SONUÇ: DEDİ\n\n"
            "⏱ Sayaç sıfırlandı.\n\n"
            f"📅 {date_str}\n\n"
            f"🔗 Kaynak:\n{src}"
        )
    else:
        # demedi -> streak +1
        state["streak"] = streak + 1
        st = state["streak"]
        # kaynak yok
        main_text = (
            "Özgür Özel bugün Kürt dedi mi?\n\n"
            "⬜ SONUÇ: DEMEDİ\n\n"
            f"⏱ {st} gündür “Kürt” demiyor.\n\n"
            f"📅 {date_str}"
        )

    tweet_with_reply(main_text)

    daily[slot_key] = True
    state["daily"][today_key] = daily
    save_state(state)


if __name__ == "__main__":
    main()
