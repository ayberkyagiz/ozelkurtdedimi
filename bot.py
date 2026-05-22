import os
import json
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import tweepy
import tempfile

from generate_monthly_report import make_monthly_report

BASE = "https://chp.org.tr"
GUNDEM_URL = f"{BASE}/gundem/"
TZ = ZoneInfo("America/New_York")

STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"
UA = "ozelkurtdedimi-bot/11.0"

AYLAR_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


# -------------------------
# Utilities
# -------------------------
def tr_date_str(d: date) -> str:
    return f"{d.day} {AYLAR_TR[d.month]} {d.year}"


def chp_date_str(d: date) -> str:
    return d.strftime("%d.%m.%Y")


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


def tweet_simple(text: str) -> None:
    x_client().create_tweet(text=text)


def tweet_with_media(text: str, media_path: str) -> None:
    media = x_api_v1().media_upload(media_path)
    x_client().create_tweet(text=text, media_ids=[str(media.media_id)])


def log_tweet_error(label: str, exc: Exception) -> None:
    print(f"{label} failed:", repr(exc))
    response = getattr(exc, "response", None)
    if response is not None:
        print("X API status:", getattr(response, "status_code", "unknown"))
        try:
            print("X API response:", response.text)
        except Exception:
            pass


def try_tweet_simple(text: str, label: str) -> bool:
    try:
        tweet_simple(text)
        return True
    except Exception as e:
        log_tweet_error(label, e)
        return False


def try_tweet_with_media(text: str, media_path: str, label: str) -> bool:
    try:
        tweet_with_media(text, media_path)
        return True
    except Exception as e:
        log_tweet_error(label, e)
        return False


# -------------------------
# State + cleanup
# -------------------------
def load_state() -> dict:
    default = {
        "daily": {},
        "streak": 0,
        "streak_result": "",
        "last_monthly_post": "",
        "last_weekly_post": "",
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
    if not isinstance(data.get("last_weekly_post"), str):
        data["last_weekly_post"] = ""
    if data.get("streak_result") not in ("dedi", "demedi"):
        data["streak_result"] = ""
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


def append_history(d: date, spoke: bool, kurt: bool, url: str | None) -> None:
    ensure_history_header()
    new_row = {
        "date": d.isoformat(),
        "spoke": "Y" if spoke else "N",
        "kurt": "Y" if kurt else "N",
        "url": url or "",
    }
    rows = []
    replaced = False
    with open(HISTORY_FILE, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date") == new_row["date"]:
                rows.append(new_row)
                replaced = True
            else:
                rows.append(row)
    if not replaced:
        rows.append(new_row)
    rows.sort(key=lambda row: row.get("date", ""))
    with open(HISTORY_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "spoke", "kurt", "url"])
        writer.writeheader()
        writer.writerows(rows)


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


def stats_between(start: date, end: date) -> dict:
    stats = {"spoken_days": 0, "kurt_yes": 0, "kurt_no": 0}
    if not os.path.exists(HISTORY_FILE):
        return stats
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(row["date"]).date()
            except Exception:
                continue
            if d < start or d > end:
                continue
            if row.get("spoke") != "Y":
                continue
            stats["spoken_days"] += 1
            if row.get("kurt") == "Y":
                stats["kurt_yes"] += 1
            else:
                stats["kurt_no"] += 1
    return stats


def previous_month(today: date) -> tuple[str, int, int]:
    y, m = today.year, today.month
    if m == 1:
        return (f"{y-1}-12", y - 1, 12)
    return (f"{y}-{m-1:02d}", y, m - 1)


def monthly_stats_text(key: str, stats: dict) -> str:
    return (
        f'Mayıs 2026\'dan bu yana aylık "Kürt" kelimesi kullanım oranı grafiği güncellendi.\n\n'
        f"{key} ayı özeti:\n\n"
        f"Konuştuğu gün sayısı: {stats['spoken_days']}\n"
        f'"Kürt" dediği konuşma günü: {stats["kurt_yes"]}\n'
        f'"Kürt" demediği konuşma günü: {stats["kurt_no"]}\n\n'
        f"Kaynak: {GUNDEM_URL}"
    )


def weekly_stats_text(start: date, end: date, stats: dict) -> str:
    return (
        f"Haftalık özet ({tr_date_str(start)} - {tr_date_str(end)}):\n\n"
        f"Konuştuğu gün sayısı: {stats['spoken_days']}\n"
        f'"Kürt" dediği konuşma günü: {stats["kurt_yes"]}\n'
        f'"Kürt" demediği konuşma günü: {stats["kurt_no"]}\n\n'
        f"Kaynak: {GUNDEM_URL}"
    )


def next_spoken_streak(state: dict, result: str) -> int:
    current = int(state.get("streak", 0))
    if state.get("streak_result") == result:
        current += 1
    else:
        current = 1
    state["streak"] = current
    state["streak_result"] = result
    return current


def streak_text(result: str, streak: int) -> str:
    verb = "diyor" if result == "dedi" else "demiyor"
    return f'{streak} konuşma günüdür "Kürt" {verb}.'


def post_monthly_stats_if_due(state: dict, today: date, override: str) -> None:
    if override or today.day != 1:
        return
    stat_key, stat_year, stat_month = previous_month(today)
    if state.get("last_monthly_post") == stat_key:
        return
    stats = monthly_stats_spoken_only(stat_year, stat_month)
    if stats["spoken_days"] <= 0:
        return
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        report_path = f.name
    try:
        make_monthly_report(stat_year, stat_month, out=report_path)
        msg = monthly_stats_text(stat_key, stats)
        state["last_monthly_post"] = stat_key
        save_state(state)
        print("Posting monthly stats image...")
        try_tweet_with_media(msg, report_path, "Monthly stats tweet")
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


def post_weekly_stats_if_due(state: dict, today: date, override: str) -> None:
    if override or today.weekday() != 6:
        return
    week_start = today - timedelta(days=6)
    week_key = today.isoformat()
    if state.get("last_weekly_post") == week_key:
        return
    stats = stats_between(week_start, today)
    if stats["spoken_days"] <= 0:
        return
    state["last_weekly_post"] = week_key
    save_state(state)
    print("Posting weekly stats...")
    try_tweet_simple(weekly_stats_text(week_start, today, stats), "Weekly stats tweet")


# -------------------------
# CHP parsing
# -------------------------
def parse_article_date(soup: BeautifulSoup) -> datetime | None:
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


def lider_anchor_text(text: str) -> bool:
    t = (text or "").lower()
    return any(
        marker in t
        for marker in (
            "kemal kılıçdaroğlu",
            "kemal kilicdaroglu",
            "chp lideri kılıçdaroğlu",
            "chp lideri kilicdaroglu",
            "genel başkanı kılıçdaroğlu",
            "genel baskani kilicdaroglu",
            "kılıçdaroğlu",
            "kilicdaroglu",
        )
    )


def find_lider_links_for_date(target: date, max_pages: int = 8) -> list[str]:
    target_token = chp_date_str(target)
    seen = set()
    matches = []
    for page in range(1, max_pages + 1):
        url = GUNDEM_URL if page == 1 else f"{GUNDEM_URL}?page={page}"
        soup = BeautifulSoup(fetch(url), "html.parser")
        page_had_target = False
        for a in soup.select("a[href]"):
            text = " ".join((a.get_text(" ", strip=True) or "").split())
            if target_token not in text:
                continue
            page_had_target = True
            if not lider_anchor_text(text):
                continue
            href = normalize_url(urljoin(BASE, a.get("href") or ""))
            if href.startswith(BASE) and href not in seen:
                seen.add(href)
                matches.append(href)
        if matches and not page_had_target:
            break
    return matches


def extract_article_text(article_html: str) -> str:
    soup = BeautifulSoup(article_html, "html.parser")
    # Menü, nav, header, footer, script, style kaldır
    for tag in soup.select("nav, header, footer, script, style, .menu, .navigation"):
        tag.decompose()
    # Önce makale ana içeriğini bulmaya çalış
    article = soup.select_one("article, .article-content, .journal-detail, .news-detail, main")
    if article:
        return "\n".join(
            p.get_text(" ", strip=True)
            for p in article.select("p")
            if p.get_text(strip=True)
        ).strip()
    # Fallback: tüm p tagları
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
        post_monthly_stats_if_due(state, today, override)
        post_weekly_stats_if_due(state, today, override)
        return

    # -------------------------
    # CHP kontrol
    # -------------------------
    article_urls = find_lider_links_for_date(today)
    spoke_now = bool(article_urls)
    kurt_now = False
    source_url = article_urls[0] if article_urls else None

    for article_url in article_urls:
        try:
            text = extract_article_text(fetch(article_url))
            if contains_kurt(text):
                kurt_now = True
                source_url = article_url
                break
        except Exception as e:
            print(f"Article fetch/parse error for {article_url}:", repr(e))

    print(f"Detected: spoke_now={spoke_now}, kurt_now={kurt_now}, url={source_url}")

    # -------------------------
    # Tweet
    # -------------------------
    if not spoke_now:
        main_text = (
            f"{date_str}\n"
            "Kemal Kılıçdaroğlu Kürt dedi mi? Konuşmadı."
        )
        print("Posting (no speech) tweet…")
        try_tweet_simple(main_text, "No speech tweet")
        append_history(today, spoke=False, kurt=False, url=None)

    elif kurt_now:
        streak = next_spoken_streak(state, "dedi")
        main_text = (
            f"{date_str}\n"
            "Kemal Kılıçdaroğlu Kürt dedi mi? Dedi.\n\n"
            f"{streak_text('dedi', streak)}\n\n"
            f"Kaynak:\n{source_url or GUNDEM_URL}"
        )
        print("Posting (kurt said) tweet…")
        try_tweet_simple(main_text, "Kurt said tweet")
        append_history(today, spoke=True, kurt=True, url=source_url)

    else:
        streak = next_spoken_streak(state, "demedi")
        main_text = (
            f"{date_str}\n"
            "Kemal Kılıçdaroğlu Kürt dedi mi? Demedi.\n\n"
            f"{streak_text('demedi', streak)}"
        )
        print("Posting (kurt NOT said) tweet…")
        try_tweet_simple(main_text, "Kurt not said tweet")
        append_history(today, spoke=True, kurt=False, url=source_url)

    daily["done"] = True
    daily["spoke_any"] = spoke_now
    daily["kurt_any"] = kurt_now
    daily["last_url"] = source_url
    state["daily"][today_key] = daily
    save_state(state)
    post_monthly_stats_if_due(state, today, override)
    post_weekly_stats_if_due(state, today, override)

    print("Done.")


if __name__ == "__main__":
    main()
