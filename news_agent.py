import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests

# Źródła RSS - możesz dowolnie dodawać/usuwać (sprawdź w logu, czy linki działają)
FEEDS = {
    "Rynki światowe": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    ],
    "Świat": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "Polska - ogólne": [
        "https://www.polsatnews.pl/rss/polska.xml",
        "https://tvn24.pl/wiadomosci-z-kraju,3.xml",
    ],
    "Polska - biznes i rynki": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gielda.xml",
        "https://www.bankier.pl/rss/waluty.xml",
        "https://www.polsatnews.pl/rss/biznes.xml",
    ],
    "E-MTB - nowości": [
        "https://ebike-mtb.com/en/feed/",
        "https://enduro-mtb.com/en/feed/",
    ],
}

MAX_PER_FEED = 10


# Lokalizacja prognozy - domyślnie Warszawa (zmień w sekretach/zmiennych lub tutaj)
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
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                print(f"Źródło OK ({len(feed.entries)} wpisów): {url}")
            except Exception as e:
                print("Błąd źródła:", url, e)
                continue
            for entry in feed.entries[:MAX_PER_FEED]:
                title = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:300]
                parts.append(f"- {title}: {summary}")
    return "\n".join(parts)


# Modele próbowane po kolei - jeśli któryś nie odpowiada, skrypt przechodzi do kolejnego
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def summarize(news: str):
    """Streszcza wiadomości przez darmowe Gemini API. Zwraca None, gdy nic nie zadziała."""
    import time

    prompt = f"""Poniżej są nagłówki z dzisiejszych wiadomości, podzielone na sekcje.
Przygotuj po polsku zwięzły poranny briefing w formacie Markdown, w tej kolejności:

## Rynki światowe
5-7 najważniejszych punktów (giełdy, waluty, surowce, banki centralne).

## Świat
5-7 najważniejszych wydarzeń.

## Polska - ogólne
5-7 najważniejszych wydarzeń w kraju (polityka, społeczeństwo, bezpieczeństwo).

## Polska - biznes i rynki
5-7 najważniejszych punktów (GPW i WIG20, złoty, NBP i RPP, inflacja, spółki, gospodarka).

## Co obserwować dziś
Jedno zdanie.

Na samym końcu dodaj dokładnie takie dwa znaczniki (każdy w osobnej linii) wraz z treścią:

@@EMTB_WERDYKT@@
Werdykt dla właściciela e-MTB: 4-6 zdań po polsku. Oprzyj się WYŁĄCZNIE na sekcji "DANE POGODOWE E-MTB" poniżej, nie zmieniaj liczb i nie dodawaj własnych prognoz. Wskaż, gdzie najlepiej jechać w najbliższy weekend i w kolejny (Warszawa, Szczyrk czy Limone sul Garda), ostrzeż o głównych ryzykach (burze, wiatr, błoto, chłód i krótszy zasięg baterii, krótki dzień) i uczciwie zaznacz, gdy pewność prognozy jest niska lub prognoza jest orientacyjna.
@@EMTB_NEWS@@
3-5 punktów po polsku z sekcji "E-MTB - nowości" (testy, nowe modele, silniki, baterie, szlaki, porady). Pomiń reklamy i pozycje niezwiązane z e-MTB. Jeśli brak danych, napisz jedno zdanie.

Zasady: pisz konkretnie, bez wstępów, każdy punkt jako lista punktowana.
Każdą sekcję opieraj wyłącznie na danych z odpowiadającej jej sekcji poniżej.
Nie wymyślaj faktów spoza podanych nagłówków. Jeśli sekcja ma za mało danych, napisz to krótko.

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


def split_ai(text: str):
    """Rozdziela odpowiedź AI na: główny briefing, werdykt e-MTB i nowości e-MTB."""
    main, verdict, emtb_news = text, "", ""
    if "@@EMTB_WERDYKT@@" in text:
        main, rest = text.split("@@EMTB_WERDYKT@@", 1)
        if "@@EMTB_NEWS@@" in rest:
            verdict, emtb_news = rest.split("@@EMTB_NEWS@@", 1)
        else:
            verdict = rest
    strip_chars = " *\n"
    return main.strip(strip_chars), verdict.strip(strip_chars), emtb_news.strip(strip_chars)


def build_emtb_markdown(data, verdict: str, emtb_news: str) -> str:
    """Składa całą rubrykę E-MTB w Markdown."""
    if not data:
        return ""
    parts = ["# 🚵 E-MTB - Warszawa, Szczyrk, Limone sul Garda\n"]
    parts.append("## Ranking najbliższych weekendów\n\n" + data["ranking"] + "\n")
    parts.append(
        "_Ocena jazdy 0-100: 🟢 80+ świetnie, 🟡 60-79 dobrze, 🟠 40-59 średnio, 🔴 poniżej 40 słabo._\n"
    )
    if verdict:
        parts.append("## Werdykt\n\n" + verdict + "\n")
    parts.append("## Szczegóły weekendów\n\n" + data["weekends"])
    parts.append("## Najbliższe 7 dni\n\n" + data["outlook"])
    if emtb_news:
        parts.append("## Nowości ze świata e-MTB\n\n" + emtb_news + "\n")
    parts.append(
        "## Jak liczymy prognozę\n\n"
        f"- Modele: {data['models']}. Wynik to **mediana modeli**, a **pewność** pokazuje, jak bardzo się zgadzają "
        "(opady tak/nie, rozrzut temperatury i opadów). Powyżej 7 dni prognozy są orientacyjne.\n"
        "- **Ocena jazdy** to autorski wskaźnik: opad, burze, temperatura, porywy wiatru, śnieg/szron "
        "oraz wilgotność szlaków (opady z ostatnich 3 dni).\n"
        "- **Szlaki** to szacunek z opadów, a nie pomiar. **Okno bez deszczu** liczone od 8:00 do zachodu słońca.\n"
        "- Symbole: ⛈ burze, ❄️ śnieg, 🧊 przymrozek, 🔋chłód (niska temperatura skraca zasięg baterii), 💨 silne porywy, 🥵 upał.\n"
        "- Przed wyjazdem sprawdź lokalne komunikaty i ewentualne zamknięcia szlaków. "
        "Dla Szczyrku temperatura jest przeliczona na ok. 800 m n.p.m., dla Limone prognoza dotyczy okolic jeziora "
        "(wyżej na szlakach bywa chłodniej).\n"
    )
    return "\n".join(parts)


def save_briefing(weather: str, briefing: str, emtb_md: str = "") -> None:
    """Zapisuje briefing do repozytorium: archiwum (briefings/) i stronę główną (README.md)."""
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()

    # pogoda jako lista punktów, żeby ładnie wyglądała na GitHubie
    lines = weather.split("\n")
    weather_md = f"## {lines[0]}\n\n" + "\n".join(f"- {line}" for line in lines[1:])

    content = (
        f"# Briefing z {today:%d.%m.%Y}\n\n"
        f"{weather_md}\n\n---\n\n"
        f"{briefing}\n"
    )
    if emtb_md:
        content += f"\n---\n\n{emtb_md}\n"

    Path("briefings").mkdir(exist_ok=True)
    Path(f"briefings/{today:%Y-%m-%d}.md").write_text(content, encoding="utf-8")
    Path("README.md").write_text(content, encoding="utf-8")


if __name__ == "__main__":
    try:
        weather = get_weather()
    except Exception as e:  # pogoda nie może zablokować zapisu wiadomości
        print("Nie udało się pobrać pogody:", e)
        weather = "POGODA - brak danych dzisiaj."

    # Rubryka E-MTB (jeśli coś pójdzie nie tak, reszta briefingu i tak się zapisze)
    emtb_data = None
    try:
        import emtb
        emtb_data = emtb.build_emtb(WMO)
    except Exception as e:
        print("Rubryka E-MTB: błąd:", e)

    headlines = collect_news()
    news_for_ai = headlines
    if emtb_data:
        news_for_ai += (
            "\n\n## DANE POGODOWE E-MTB (obliczone automatycznie, nie zmieniaj liczb)\n"
            + emtb_data["llm_text"]
        )

    ai_text = summarize(news_for_ai)
    verdict, emtb_news = "", ""
    if ai_text is None:
        briefing = (
            "Streszczenie AI jest dzisiaj niedostępne. Surowe nagłówki:\n\n"
            + headlines[:4000]
        )
    else:
        briefing, verdict, emtb_news = split_ai(ai_text)

    save_briefing(weather, briefing, build_emtb_markdown(emtb_data, verdict, emtb_news))
    print("Zapisano briefing.")
