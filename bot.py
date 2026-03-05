import requests
from bs4 import BeautifulSoup
import tweepy
import datetime
import os
import json

URL = "https://twitter.com/ozelkurtdedimi"

client = tweepy.Client(
    consumer_key=os.environ["X_API_KEY"],
    consumer_secret=os.environ["X_API_KEY_SECRET"],
    access_token=os.environ["X_ACCESS_TOKEN"],
    access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"]
)

STATE_FILE = "state.json"


def load_state():

    if not os.path.exists(STATE_FILE):
        return {"last_tweet_date": "", "streak": 0}

    with open(STATE_FILE, "r") as f:
        data = json.load(f)

    if "last_tweet_date" not in data:
        data["last_tweet_date"] = ""

    if "streak" not in data:
        data["streak"] = 0

    return data


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def check_site():

    r = requests.get(URL)

    soup = BeautifulSoup(r.text, "html.parser")

    text = soup.get_text().lower()

    return "kürt" in text


def turkish_date():

    today = datetime.date.today()

    months = {
        1: "Ocak",
        2: "Şubat",
        3: "Mart",
        4: "Nisan",
        5: "Mayıs",
        6: "Haziran",
        7: "Temmuz",
        8: "Ağustos",
        9: "Eylül",
        10: "Ekim",
        11: "Kasım",
        12: "Aralık"
    }

    return f"{today.day} {months[today.month]} {today.year}"


def send_tweet(text):

    tweet = client.create_tweet(text=text)

    tweet_id = tweet.data["id"]

    reply = (
        "🔁 Takip etmek için takip edin.\n\n"
        "📊 Son 30 gün istatistiği yakında paylaşılacak."
    )

    client.create_tweet(
        text=reply,
        in_reply_to_tweet_id=tweet_id
    )


def main():

    today = str(datetime.date.today())

    state = load_state()

    if state["last_tweet_date"] == today:
        print("Bugün tweet zaten atıldı")
        return

    said = check_site()

    date_str = turkish_date()

    if said:

        state["streak"] = 0

        tweet_text = f"""{date_str}

Özgür Özel bugün Kürt dedi mi?

🟥 SONUÇ: DEDİ

⏱ Sayaç sıfırlandı.

Kaynak:
{URL}
"""

    else:

        state["streak"] += 1

        tweet_text = f"""{date_str}

Özgür Özel bugün Kürt dedi mi?

⬜ SONUÇ: DEMEDİ

⏱ {state["streak"]} gündür “Kürt” demiyor.

Kaynak:
{URL}
"""

    send_tweet(tweet_text)

    state["last_tweet_date"] = today

    save_state(state)


if __name__ == "__main__":
    main()
