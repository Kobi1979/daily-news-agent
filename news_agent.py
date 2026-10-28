import os
import smtplib
from datetime import date
from email.mime.text import MIMEText

import feedparser
import requests

# Źródła RSS - możesz dowolnie dodawać/usuwać (sprawdź, czy linki działają)
FEEDS = {
    "Rynki": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    ],
    "Świat": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
}

MAX_PER_FEED = 10


# Lokalizacja prognozy - domyślnie Warszawa
CITY = os.environ.get("WEATHER_CITY", "Warszawa")
LAT = os.environ.get("WEATHER_LAT", "52.2297")
LON = os.environ.get("WEATHER_LON", "21.0122")

# Kody pogody WMO -> opis po polsku
WMO = {
    0: "bezchmurnie", 1: "przeważnie słonecznie", 2: "częściowe zachmurzenie",
    3: "pochmurno", 45: "mgła", 48: "mgła szronowa",
    51: "lekka mżawka", 53: "mżawka", 55: "gęsta mżawka",
    56: "marznąca mżawka", 57: "silna marznąca mżawka",
    61: "słaby deszcz", 63: "umiarkowany deszcz", 65: "silny deszcz",
    66: "marznący deszcz", 67: "silny marznący deszcz",
    71: "słaby śnieg", 73: "umiarkowany śnieg", 75: "silny śnieg",
    77: "ziarna śniegu", 80: "słabe przelotne opady", 81: "przelotne opady",
    82: "silne przelotne opady", 85: "przelotny śnieg", 86: "silny przelotny śnieg",
    95: "burza", 96: "burza z gradem", 99: "silna burza z gradem",
}


def get_weather() -> str:
    """Pobiera dzisiejszą prognozę z Open-Meteo (darmowe, bez klucza)."""
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LAT,
            "longitude": LON,
            "daily": ",".join([
                "weather_code", "temperature_2m_max", "temperature_2m_min",
                "apparent_temperature_max", "apparent_temperature_min",
                "precipitation_sum", "precipitation_probability_max",
                "wind_speed_10m_max", "wind_gusts_10m_max",
                "uv_index_max", "sunrise", "sunset",
            ]),
            "timezone": "Europe/Warsaw",
            "forecast_days": 1,
        },
        timeout=60,
    )
    response.raise_for_status()
    d = response.json()["daily"]

    def v(key):
        return d[key][0]

    return (
        f"POGODA - {CITY}, {date.today():%d.%m.%Y}\n"
        f"{WMO.get(v('weather_code'), 'brak opisu').capitalize()}\n"
        f"Temperatura: {v('temperature_2m_min'):.0f}°C do {v('temperature_2m_max'):.0f}°C "
        f"(odczuwalna {v('apparent_temperature_min'):.0f}°C do {v('apparent_temperature_max'):.0f}°C)\n"
        f"Opady: {v('precipitation_sum'):.1f} mm, prawdopodobieństwo do {v('precipitation_probability_max')}%\n"
        f"Wiatr: do {v('wind_speed_10m_max'):.0f} km/h, w porywach {v('wind_gusts_10m_max'):.0f} km/h\n"
        f"Indeks UV: {v('uv_index_max'):.0f}\n"
        f"Wschód słońca: {v('sunrise')[-5:]}, zachód: {v('sunset')[-5:]}"
    )


def collect_news() -> str:
    """Zbiera tytuły i krótkie opisy z kanałów RSS do jednego tekstu."""
    parts = []
    for section, urls in FEEDS.items():
        parts.append(f"## {section}")
        for url in urls:
            try:
                resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                feed = feedparser.parse(resp.content)
            except Exception as e:
                print("Błąd źródła:", url, e)
                continue
            for entry in feed.entries[:MAX_PER_FEED]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")[:300]
                parts.append(f"- {title}: {summary}")
    return "\n".join(parts)


# Modele próbowane po kolei - jeśli któryś nie odpowiada, skrypt przechodzi do kolejnego
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def summarize(news: str):
    """Streszcza wiadomości przez darmowe Gemini API. Zwraca None, gdy nic nie zadziała."""
    import time

    prompt = f"""Poniżej są nagłówki z dzisiejszych wiadomości (rynki oraz świat).
Przygotuj po polsku zwięzły poranny briefing:

1. RYNKI - 5-7 najważniejszych punktów (giełdy, waluty, surowce, banki centralne).
2. ŚWIAT - 5-7 najważniejszych wydarzeń.
3. Na koniec jedno zdanie: co warto obserwować dziś.

Pisz konkretnie, bez wstępów. Nie wymyślaj faktów spoza podanych nagłówków.

{news}"""
    api_key = os.environ["GEMINI_API_KEY"]

    for model in GEMINI_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        # do 4 prób na model - błąd 503 ("duży ruch") zwykle mija po chwili
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=60,
                )
            except requests.RequestException as e:
                print(f"Model {model}, próba {attempt}: brak odpowiedzi: {e}")
                break

            if response.ok:
                try:
                    parts = response.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts)
                    if text.strip():
                        print("Streszczenie wykonał model:", model)
                        return text
                except (KeyError, IndexError, ValueError) as e:
                    print(f"Model {model} zwrócił nieoczekiwaną odpowiedź: {e}")
                break

            print(f"Model {model}, próba {attempt}: błąd {response.status_code} {response.text[:200]}")
            if response.status_code in (429, 500, 503) and attempt < 4:
                time.sleep(20 * attempt)  # czekamy 20, 40, 60 sekund i próbujemy znowu
                continue
            break

    return None


def send_email(body: str) -> None:
    user = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]
    to = os.environ["EMAIL_TO"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Briefing: rynki i świat - {date.today():%d.%m.%Y}"
    msg["From"] = user
    msg["To"] = to

    host = os.environ.get("SMTP_HOST", "poczta.interia.pl")
    port = int(os.environ.get("SMTP_PORT", "465"))

    with smtplib.SMTP_SSL(host, port, timeout=60) as server:
        server.login(user, password)
        server.send_message(msg)


if __name__ == "__main__":
    try:
        weather = get_weather()
    except Exception as e:  # pogoda nie może zablokować wysyłki wiadomości
        print("Nie udało się pobrać pogody:", e)
        weather = "POGODA - brak danych dzisiaj."

    news = collect_news()
    briefing = summarize(news)
    if briefing is None:
        briefing = (
            "Streszczenie AI jest dzisiaj niedostępne. Surowe nagłówki:\n\n"
            + news[:4000]
        )

    send_email(weather + "\n\n" + "-" * 30 + "\n\n" + briefing)
    print("Wysłano briefing.")
