import os
import requests
from bs4 import BeautifulSoup
import tweepy
from datetime import datetime

BASE = "https://chp.org.tr"
URL = "https://chp.org.tr/gundem/"

def check_kurt():
    r = requests.get(URL)
    soup = BeautifulSoup(r.text, "html.parser")

    links = []

    for a in soup.find_all("a"):
        text = a.get_text().lower()
        if "özgür özel" in text:
            href = a.get("href")
            if href and href.startswith("/"):
                links.append(BASE + href)

    if not links:
        return None, None

    article = requests.get(links[0])
    soup = BeautifulSoup(article.text, "html.parser")

    text = soup.get_text().lower()

    if "kürt" in text:
        return True, links[0]
    else:
        return False, links[0]


def tweet(result, link):

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"]
    )

    today = datetime.now().strftime("%d %B %Y")

    if result:
        msg = f"{today}: Dedi.\n\n🔗 Kaynak: {link}"
    else:
        msg = f"{today}: Demedi.\n\n🔗 Kaynak: {link}"

    client.create_tweet(text=msg)


result, link = check_kurt()

if result is not None:
    tweet(result, link)
