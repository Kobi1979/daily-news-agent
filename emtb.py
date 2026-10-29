"""Rubryka E-MTB: prognozy pogody dla Warszawy, Szczyrku i Limone sul Garda.

Dane z Open-Meteo (darmowe, bez klucza). Prognozę liczymy jako medianę kilku
modeli pogodowych, a zgodność modeli pokazujemy jako "pewność" prognozy.
"""
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API_URL = "https://api.open-meteo.com/v1/forecast"

# Miejsca - możesz zmieniać współrzędne, wysokość ("elev", w metrach) i nazwy.
# elev = None oznacza, że wysokość ustala sam Open-Meteo.
LOCATIONS = [
    {
        "name": "Warszawa", "lat": 52.2297, "lon": 21.0122, "elev": None,
        "tz": "Europe/Warsaw", "info": "np. Kampinos, Las Kabacki",
    },
    {
        "name": "Szczyrk", "lat": 49.7186, "lon": 19.0334, "elev": 800,
        "tz": "Europe/Warsaw",
        "info": "np. Skrzyczne, Szczyrk Mountain Resort; temperatura przeliczona dla ok. 800 m n.p.m.",
    },
    {
        "name": "Limone sul Garda", "lat": 45.8133, "lon": 10.7922, "elev": None,
        "tz": "Europe/Rome",
        "info": "np. Tremalzo, Ponale; prognoza dla okolic jeziora, na szlakach powyżej 1000 m bywa o kilka stopni chłodniej",
    },
]

MODEL_LABELS = {
    "ecmwf_ifs": "ECMWF IFS (9 km)",
    "ecmwf_aifs025_single": "ECMWF AIFS (model AI)",
    "gfs_seamless": "NOAA GFS",
    "icon_seamless": "DWD ICON",
    "meteofrance_seamless": "Météo-France",
}

# Jeśli któryś model nie jest dostępny (błąd 400), próbujemy mniejszych zestawów
MODEL_SETS = [
    list(MODEL_LABELS),
    ["ecmwf_ifs", "gfs_seamless", "icon_seamless"],
    ["best_match"],
]

DAILY_VARS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum", "precipitation_probability_max", "snowfall_sum",
    "wind_gusts_10m_max",
]
HOURLY_VARS = ["precipitation", "wind_gusts_10m", "cape"]

DAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
DAYS_SHORT = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"]


# ---------------------------------------------------------------- pobieranie

def _fetch(loc, models):
    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "daily": ",".join(DAILY_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "models": ",".join(models),
        "timezone": loc["tz"],
        "forecast_days": 16,
        "past_days": 3,
    }
    if loc.get("elev") is not None:
        params["elevation"] = loc["elev"]
    response = requests.get(API_URL, params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def _fetch_sun(loc):
    """Wschody i zachody słońca (osobne, proste zapytanie)."""
    try:
        r = requests.get(
            API_URL,
            params={
                "latitude": loc["lat"], "longitude": loc["lon"],
                "daily": "sunrise,sunset", "timezone": loc["tz"], "forecast_days": 16,
            },
            timeout=60,
        )
        r.raise_for_status()
        d = r.json()["daily"]
        return {t: (sr, ss) for t, sr, ss in zip(d["time"], d["sunrise"], d["sunset"])}
    except Exception as e:
        print("E-MTB: brak godzin słońca dla", loc["name"], e)
        return {}


def fetch_location(loc):
    last_error = None
    for models in MODEL_SETS:
        try:
            data = _fetch(loc, models)
            print(f"E-MTB: {loc['name']} - modele: {', '.join(models)}")
            return {
                "loc": loc, "models": models,
                "daily": data["daily"], "hourly": data.get("hourly", {}),
                "sun": _fetch_sun(loc),
            }
        except Exception as e:
            last_error = e
            print(f"E-MTB: {loc['name']} - zestaw modeli {models} nie zadziałał: {e}")
    raise RuntimeError(f"Brak danych dla {loc['name']}: {last_error}")


# ---------------------------------------------------------------- obliczenia

def _series(block, var, model, multi):
    if multi:
        return block.get(f"{var}_{model}")
    return block.get(var) or block.get(f"{var}_{model}")


def _vals(block, var, i, models):
    multi = len(models) > 1
    out = []
    for m in models:
        arr = _series(block, var, m, multi)
        if arr and i < len(arr) and arr[i] is not None:
            out.append(arr[i])
    return out


def _med(vals):
    return statistics.median(vals) if vals else None


def build_records(res):
    daily, models = res["daily"], res["models"]
    recs = []
    for i, t in enumerate(daily["time"]):
        tmax = _vals(daily, "temperature_2m_max", i, models)
        tmin = _vals(daily, "temperature_2m_min", i, models)
        prec = _vals(daily, "precipitation_sum", i, models)
        prob = _vals(daily, "precipitation_probability_max", i, models)
        snow = _vals(daily, "snowfall_sum", i, models)
        gust = _vals(daily, "wind_gusts_10m_max", i, models)
        code = _vals(daily, "weather_code", i, models)
        storms = sum(1 for c in code if c >= 95)
        recs.append({
            "i": i,
            "date": date.fromisoformat(t),
            "tmax": _med(tmax), "tmin": _med(tmin),
            "precip": _med(prec),
            "prob": statistics.mean(prob) if prob else None,
            "snow": _med(snow), "gust": _med(gust),
            "code": int(max(statistics.multimode(code))) if code else None,
            "storm": storms >= 2 or (len(code) <= 2 and storms >= 1),
            "n_models": len(prec),
            "tspread": (max(tmax) - min(tmax)) if len(tmax) > 1 else None,
            "pspread": (max(prec) - min(prec)) if len(prec) > 1 else None,
            "wet_votes": sum(1 for p in prec if p >= 1.0),
        })
    return recs


def wet_index(recs, i):
    """Szacunek wilgotności szlaków: opady z 3 poprzednich dni + część dzisiejszych."""
    def p(k):
        if 0 <= k < len(recs) and recs[k]["precip"] is not None:
            return recs[k]["precip"]
        return 0.0
    return p(i - 1) + 0.6 * p(i - 2) + 0.3 * p(i - 3) + 0.5 * p(i)


def trail_state(rec, wet):
    if rec["snow"] is not None and rec["snow"] >= 0.5:
        return "śnieg"
    if rec["tmax"] is not None and rec["tmax"] <= 0:
        return "zamarznięte/śliskie"
    if wet < 1:
        return "suche"
    if wet < 4:
        return "lekko wilgotne"
    if wet < 10:
        return "mokre, błoto miejscami"
    return "błotniste"


def ride_score(rec, wet):
    if rec["tmax"] is None or rec["precip"] is None:
        return None
    s = 100
    p = rec["precip"]
    s -= 60 if p >= 8 else 35 if p >= 3 else 15 if p >= 1 else 5 if p >= 0.2 else 0
    if rec["storm"]:
        s -= 40
    t = rec["tmax"]
    if t < 0:
        s -= 45
    elif t < 5:
        s -= 35
    elif t < 8:
        s -= 20
    elif t < 12:
        s -= 8
    elif t > 32:
        s -= 40
    elif t > 28:
        s -= 20
    elif t > 24:
        s -= 5
    if rec["tmin"] is not None and rec["tmin"] < 0:
        s -= 10
    g = rec["gust"]
    if g is not None:
        s -= 40 if g >= 60 else 20 if g >= 45 else 8 if g >= 30 else 0
    if rec["snow"] is not None and rec["snow"] >= 0.5:
        s -= 25
    s -= 15 if wet > 10 else 7 if wet >= 4 else 0
    return max(0, min(100, round(s)))


def score_label(score):
    if score is None:
        return "⚪", "brak danych"
    if score >= 80:
        return "🟢", "świetnie"
    if score >= 60:
        return "🟡", "dobrze"
    if score >= 40:
        return "🟠", "średnio"
    return "🔴", "słabo"


def confidence(rec, lead):
    k = rec["n_models"]
    if k < 2:
        return "niska"
    pts = 0
    if rec["wet_votes"] in (0, k):
        pts += 1
    if rec["tspread"] is not None and rec["tspread"] <= 3:
        pts += 1
    if rec["pspread"] is not None and rec["pspread"] <= 3:
        pts += 1
    if k >= 3:
        pts += 1
    conf = "wysoka" if pts >= 4 else "średnia" if pts == 3 else "niska"
    if lead >= 8 and conf == "wysoka":
        conf = "średnia"
    return conf


def riding_window(res, day, sunset=None):
    """Najdłuższe okno (od 8:00 do zachodu słońca) bez deszczu, z umiarkowanym wiatrem i bez ryzyka burz."""
    hourly, models = res["hourly"], res["models"]
    times = hourly.get("time")
    if not times:
        return None
    prefix = day.isoformat()
    last_hour = 19
    if sunset:
        last_hour = min(19, int(sunset[11:13]) - 1)
    hours = []
    for k, t in enumerate(times):
        if not t.startswith(prefix):
            continue
        h = int(t[11:13])
        if not 8 <= h <= last_hour:
            continue
        p = _vals(hourly, "precipitation", k, models)
        if not p:
            continue
        g = _vals(hourly, "wind_gusts_10m", k, models)
        c = _vals(hourly, "cape", k, models)
        good = _med(p) < 0.3 and (_med(g) if g else 0) < 45 and (_med(c) if c else 0) < 800
        hours.append((h, good))
    if not hours:
        return None
    best, cur = [], []
    for h, good in hours:
        if good and (not cur or h == cur[-1] + 1):
            cur.append(h)
        elif good:
            cur = [h]
        else:
            cur = []
        if len(cur) > len(best):
            best = cur[:]
    if len(best) < 2:
        return "brak suchego okna"
    return f"{best[0]:02d}:00-{best[-1] + 1:02d}:00"


# ---------------------------------------------------------------- formatowanie

def _flags(rec):
    f = []
    if rec["storm"]:
        f.append("⛈")
    if rec["snow"] is not None and rec["snow"] >= 0.5:
        f.append("❄️")
    if rec["tmin"] is not None and rec["tmin"] < 0:
        f.append("🧊")
    if rec["tmax"] is not None and rec["tmax"] < 7:
        f.append("🔋chłód")
    if rec["gust"] is not None and rec["gust"] >= 45:
        f.append("💨")
    if rec["tmax"] is not None and rec["tmax"] >= 30:
        f.append("🥵")
    return " ".join(f)


def _temp(rec):
    if rec["tmin"] is None or rec["tmax"] is None:
        return "-"
    return f"{rec['tmin']:.0f}...{rec['tmax']:.0f}°C"


def _rain(rec):
    if rec["precip"] is None:
        return "-"
    s = f"{rec['precip']:.1f} mm"
    if rec["prob"] is not None:
        s += f" ({rec['prob']:.0f}%)"
    return s


def _gust(rec):
    return "-" if rec["gust"] is None else f"{rec['gust']:.0f} km/h"


def next_weekends(today, n=2):
    wd = today.weekday()
    weekends = []
    if wd == 6:
        weekends.append((today, today))
        sat = today + timedelta(days=6)
    else:
        sat = today + timedelta(days=(5 - wd) % 7)
    while len(weekends) < n:
        weekends.append((sat, sat + timedelta(days=1)))
        sat += timedelta(days=7)
    return weekends


def _weekend_label(start, end, today):
    if start == end:
        label = f"niedziela {start:%d.%m}"
    else:
        label = f"{start:%d.%m}-{end:%d.%m}"
    delta = (start - today).days
    when = "dziś" if delta <= 0 else "jutro" if delta == 1 else f"za {delta} dni"
    return f"{label} ({when})"


def build_emtb(wmo):
    """Zwraca słownik z gotowymi fragmentami Markdown i tekstem dla AI (lub None)."""
    results = {}
    for loc in LOCATIONS:
        try:
            res = fetch_location(loc)
            res["recs"] = build_records(res)
            results[loc["name"]] = res
        except Exception as e:
            print("E-MTB:", e)
    if not results:
        return None

    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    weekends = next_weekends(today)
    llm = []

    def rec_for(res, day):
        for r in res["recs"]:
            if r["date"] == day:
                return r
        return None

    def evaluate(res, rec):
        wet = wet_index(res["recs"], rec["i"])
        score = ride_score(rec, wet)
        lead = (rec["date"] - today).days
        return score, trail_state(rec, wet), confidence(rec, lead), lead

    # ---- ranking weekendów
    names = [l["name"] for l in LOCATIONS]
    rank_lines = [
        "| Weekend | " + " | ".join(names) + " | Najlepszy wybór |",
        "|---|" + "---|" * (len(names) + 1),
    ]
    weekend_avgs = []
    for start, end in weekends:
        avgs, cells = {}, []
        for name in names:
            res = results.get(name)
            scores = []
            if res:
                d = start
                while d <= end:
                    r = rec_for(res, d)
                    if r:
                        sc = evaluate(res, r)[0]
                        if sc is not None:
                            scores.append(sc)
                    d += timedelta(days=1)
            if scores:
                avg = round(sum(scores) / len(scores))
                avgs[name] = avg
                cells.append(f"{score_label(avg)[0]} {avg}")
            else:
                cells.append("-")
        if avgs:
            top = max(avgs, key=avgs.get)
            best = f"**{top}**" if avgs[top] >= 45 else "brak dobrego wyboru"
        else:
            best = "-"
        weekend_avgs.append(avgs)
        rank_lines.append(f"| {_weekend_label(start, end, today)} | " + " | ".join(cells) + f" | {best} |")
    llm.append("RANKING WEEKENDÓW (średnia ocena jazdy 0-100):")
    for (start, end), avgs in zip(weekends, weekend_avgs):
        llm.append(f"- {_weekend_label(start, end, today)}: " + ", ".join(f"{k} {v}" for k, v in avgs.items()))

    # ---- szczegóły weekendów
    weekend_md = []
    for start, end in weekends:
        weekend_md.append(f"### Weekend {_weekend_label(start, end, today)}\n")
        llm.append(f"WEEKEND {_weekend_label(start, end, today)}:")
        for loc in LOCATIONS:
            res = results.get(loc["name"])
            if not res:
                weekend_md.append(f"- **{loc['name']}**: brak danych\n")
                continue
            weekend_md.append(f"- **{loc['name']}**")
            d = start
            while d <= end:
                rec = rec_for(res, d)
                d_next = d + timedelta(days=1)
                if rec:
                    score, trail, conf, lead = evaluate(res, rec)
                    emoji, label = score_label(score)
                    sun = res["sun"].get(d.isoformat())
                    window = riding_window(res, d, sun[1] if sun else None)
                    sunset = f" · zachód {sun[1][-5:]}" if sun else ""
                    desc = wmo.get(rec["code"], "brak opisu").capitalize() if rec["code"] is not None else "brak opisu"
                    flags = _flags(rec)
                    approx = " (orientacyjnie)" if lead >= 8 else ""
                    line = (
                        f"  - **{DAYS_PL[d.weekday()].capitalize()} {d:%d.%m}** - {emoji} **{score}/100** ({label}){approx} · "
                        f"{desc}{(' ' + flags) if flags else ''} · {_temp(rec)} · opad {_rain(rec)} · "
                        f"porywy {_gust(rec)} · szlaki: {trail}"
                        f"{(' · okno bez deszczu: ' + window) if window else ''}{sunset} · pewność: {conf}"
                    )
                    weekend_md.append(line)
                    llm.append(
                        f"- {loc['name']}, {DAYS_PL[d.weekday()]} {d:%d.%m}: ocena {score}/100 ({label}), {desc}, "
                        f"{_temp(rec)}, opad {_rain(rec)}, porywy {_gust(rec)}, szlaki {trail}, "
                        f"okno {window or 'brak danych'}, pewność {conf}, flagi: {flags or 'brak'}"
                    )
                d = d_next
            weekend_md.append("")
    # ---- 7 dni do przodu
    outlook_md = []
    for loc in LOCATIONS:
        res = results.get(loc["name"])
        outlook_md.append(f"### {loc['name']}")
        outlook_md.append(f"_{loc['info']}_\n")
        if not res:
            outlook_md.append("Brak danych.\n")
            continue
        outlook_md.append("| Dzień | Pogoda | Temp. | Opad | Porywy | Szlaki | Ocena | Pewność |")
        outlook_md.append("|---|---|---|---|---|---|---|---|")
        for k in range(7):
            d = today + timedelta(days=k)
            rec = rec_for(res, d)
            if not rec:
                continue
            score, trail, conf, lead = evaluate(res, rec)
            emoji, _ = score_label(score)
            desc = wmo.get(rec["code"], "-").capitalize() if rec["code"] is not None else "-"
            flags = _flags(rec)
            outlook_md.append(
                f"| {DAYS_SHORT[d.weekday()]} {d:%d.%m} | {(desc + ' ' + flags).strip()} | {_temp(rec)} | {_rain(rec)} | "
                f"{_gust(rec)} | {trail} | {emoji} {score} | {conf} |"
            )
        outlook_md.append("")

    used = []
    for res in results.values():
        for m in res["models"]:
            label = MODEL_LABELS.get(m, m)
            if label not in used:
                used.append(label)

    return {
        "ranking": "\n".join(rank_lines),
        "weekends": "\n".join(weekend_md),
        "outlook": "\n".join(outlook_md),
        "models": ", ".join(used),
        "llm_text": "\n".join(llm),
    }
