import os
import tempfile

from bot import load_state, monthly_stats_spoken_only, monthly_stats_text, save_state, try_tweet_with_media
from generate_monthly_report import make_monthly_report


def main() -> None:
    month_key = os.getenv("FORCE_MONTHLY", "").strip()
    if not month_key:
        raise RuntimeError("FORCE_MONTHLY must be YYYY-MM")
    try:
        year_s, month_s = month_key.split("-", 1)
        year = int(year_s)
        month = int(month_s)
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        raise RuntimeError("FORCE_MONTHLY must be YYYY-MM")

    stats = monthly_stats_spoken_only(year, month)
    if stats["spoken_days"] <= 0:
        raise RuntimeError(f"No monthly data found for {month_key}")

    state = load_state()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        report_path = f.name
    try:
        make_monthly_report(year, month, out=report_path)
        state["last_monthly_post"] = month_key
        save_state(state)
        print(f"Posting monthly stats image for {month_key}...")
        try_tweet_with_media(monthly_stats_text(month_key, stats), report_path, "Monthly stats tweet")
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
