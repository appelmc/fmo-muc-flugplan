#!/usr/bin/env python3
"""Baut einen iCalendar-Feed (ICS) für die Lufthansa-Verbindungen FMO <-> MUC.

Quelle: Flugplan des Flughafens Münster/Osnabrück, https://www.fmo.de/muenchen/
Die Seite zeigt von Haus aus nur vier Wochen; das Suchformular derselben Seite
lässt sich per POST mit einem beliebigen Zeitraum abfragen (Daten reichen
mindestens ein Jahr voraus). Beide Richtungen stehen auf der Antwortseite.

Zweitquelle für die Kennzeichnung "[bestätigt]": die Flugtafel des Flughafens
München (munich-airport.com). Sie ist ein eigenes System, wird aus dem
Flugverkehr gespeist und trägt je Flug auch den Flugzeugtyp. Flüge, die dort
für denselben Tag stehen, gelten als bestätigt.

Aufruf:
    python3 build_fmo_ics.py [--out PATH] [--weeks N] [--confirm-days N] [--keep-past]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.cookiejar
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

SOURCE = "https://www.fmo.de/muenchen/"
UA = "Mozilla/5.0 (compatible; fmo-muc-schedule/1.0)"
TZ = ZoneInfo("Europe/Berlin")
DEFAULT_WEEKS = 52  # maximales Fenster: die Quelle ist ueberall dieselbe
CONFIRM_DAYS = 14   # nur die ersten zwei Wochen werden gegen MUC gegengeprueft

# Flugtafel des Flughafens Muenchen (Zweitquelle, kein Schluessel noetig).
MUC_BOARD = "https://www.munich-airport.com/flightsearch/{direction}"
MUC_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": "https://www.munich-airport.com/",
    "X-Requested-With": "XMLHttpRequest",
}
MUC_ROW_RE = re.compile(r'<tr[^>]*class="fp-flight-item"[^>]*>(.*?)</tr>', re.S)
MUC_CELL_RE = re.compile(
    r'<td[^>]*class="fp-flight-(airline|airport|number|status|time-muc|time-other|area)"[^>]*>(.*?)</td>',
    re.S)

ROW_RE = re.compile(r'<a class="[^"]*flight-list-item[^"]*"(.*?)</a>', re.S)
FIELD_RE = re.compile(r'<div class="flight-list-item-(time|day|destination|airline|status)[^"]*">(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
TIME_RE = re.compile(r"(\d{2}:\d{2})")
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
FLIGHT_RE = re.compile(r"([A-Z0-9]{2}\s?\d{3,4})")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", fragment)).strip()


def flight_number_key(flight: str) -> str:
    """'LH 2143' / 'VL 2143' -> '2143' (Marke ist saisonal austauschbar)."""
    digits = re.sub(r"\D", "", flight or "")
    return digits.lstrip("0") or digits


def fetch_page(url: str = SOURCE, data: dict | None = None) -> str:
    """GET auf die Seite oder POST der Flugsuche mit eigenem Zeitraum.

    Das Suchformular (POST, multipart, ohne Token) gibt jeden Zeitraum heraus -
    die Flugplandaten reichen mindestens bis Oktober 2027.
    """
    if data is None:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
    else:
        boundary = "----fmoform"
        body = b""
        for key, value in data.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{key}"\r\n\r\n{value}\r\n').encode("utf-8")
        body += f"--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Referer": url,
        })
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def search_range(date_from: str, date_to: str) -> str:
    """Flugsuche der FMO-Seite fuer einen Zeitraum (ISO-Daten)."""
    return fetch_page(SOURCE, {
        "doFlightSearch[destination]": "MUC",
        "doFlightSearch[dateFrom]": date_from,
        "doFlightSearch[dateTo]": date_to,
        "doFlightSearch[step-2-next]": "los",
    })


def month_chunks(start: dt.date, end: dt.date):
    """Monatsweise Abschnitte, damit die Antworten handlich bleiben."""
    cur = start
    while cur <= end:
        nxt = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
        last = min(nxt - dt.timedelta(days=1), end)
        yield cur, last
        cur = last + dt.timedelta(days=1)


def collect_flights(weeks: float = DEFAULT_WEEKS) -> list[dict]:
    """Holt den Flugplan ab morgen fuer die angegebene Reichweite (Wochen)."""
    start = dt.datetime.now(TZ).date() + dt.timedelta(days=1)
    end = start + dt.timedelta(days=int(round(weeks * 7)))
    flights: list[dict] = []
    fehler: list[str] = []
    for a, b in month_chunks(start, end):
        try:
            page = search_range(a.isoformat(), b.isoformat())
            out_block, in_block = split_directions(page)
            teil = parse_block(out_block, "out") + parse_block(in_block, "in")
            if not teil:
                fehler.append(f"{a}..{b}: keine Fluege erkannt")
            flights.extend(teil)
        except Exception as exc:                       # noqa: BLE001 - Teilausfall tolerieren
            fehler.append(f"{a}..{b}: {exc}")
        time.sleep(1)
    if not flights:
        raise SystemExit("FEHLER: keine Flugdaten von fmo.de erhalten - " + "; ".join(fehler))
    if fehler:
        print("WARNUNG (Teilausfaelle): " + "; ".join(fehler), file=sys.stderr)
    return flights


# --- Gegenprobe gegen die Flugtafel des Flughafens Muenchen -----------------

def muc_opener() -> urllib.request.OpenerDirector:
    """Sitzung fuer die Muenchner Tafel: ohne Cookie der Startseite antwortet
    der Tages-Endpunkt mit HTTP 500."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(urllib.request.Request("https://www.munich-airport.com/", headers=MUC_HEADERS),
                 timeout=60):
        pass
    return op


def muc_day(direction: str, day: dt.date, opener: urllib.request.OpenerDirector) -> str:
    """Eine Tagesansicht der Muenchner Flugtafel ('arrivals'/'departures').

    Der Endpunkt liefert nur EINEN Tag je Abruf; ein Zeitraum ueber mehrere
    Tage endet in HTTP 500.
    """
    tag = day.isoformat()
    url = (MUC_BOARD.format(direction=direction)
           + f"?from={tag}T00:00:00&allow_scroll_back=1&allow_pagination=1"
           + f"&min_date={tag}T00:00:00&max_date={tag}T23:59:59&per_page=2000&page=0")
    req = urllib.request.Request(url, headers=MUC_HEADERS)
    try:
        with opener.open(req, timeout=120) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:                                   # noqa: BLE001
        # Sitzung abgelaufen: einmal neu aufbauen und wiederholen.
        with muc_opener().open(req, timeout=120) as r:
            return r.read().decode("utf-8", "replace")


def parse_muc_board(seite: str) -> list[dict]:
    """Fluege mit Ziel/Herkunft Muenster/Osnabrueck aus der Muenchner Tafel."""
    out = []
    for block in MUC_ROW_RE.findall(seite):
        if "(FMO)" not in block:
            continue
        zellen: dict[str, str] = {}
        for name, fragment in MUC_CELL_RE.findall(block):
            zellen.setdefault(name, _text(fragment))
        nummer_text = zellen.get("number", "")
        nummer = FLIGHT_RE.search(nummer_text)
        muster = re.search(r"\(([A-Z0-9]{3,4})\)", nummer_text)
        zeiten = TIME_RE.findall(zellen.get("time-muc", "")) + TIME_RE.findall(zellen.get("time-other", ""))
        out.append({
            "flight": nummer.group(1) if nummer else "",
            "aircraft": muster.group(1) if muster else "",
            "times": zeiten,
        })
    return out


def confirm_against_muc(flights: list[dict], days: int) -> tuple[set, dict, str]:
    """Gegenprobe: Fluege, die auch auf der Muenchner Tafel stehen.

    Gibt (Schluessel, Zusatzinfos, Hinweis) zurueck. Bei Fehlern bleibt die
    Menge leer - es wird nie ein Flug faelschlich als bestaetigt markiert.
    """
    if days <= 0:
        return set(), {}, "Stufe abgeschaltet"
    heute = dt.datetime.now(TZ).date()
    grenze = heute + dt.timedelta(days=days)
    tage = sorted({f["date"] for f in flights})
    tage = [t for t in tage if heute <= dt.date.fromisoformat(t) <= grenze]
    bestaetigt: set = set()
    zusatz: dict = {}
    fehler: list[str] = []
    opener = muc_opener()
    for tag in tage:
        d = dt.date.fromisoformat(tag)
        for direction, von, nach in (("arrivals", "FMO", "MUC"), ("departures", "MUC", "FMO")):
            try:
                for z in parse_muc_board(muc_day(direction, d, opener)):
                    num = flight_number_key(z["flight"])
                    if not num:
                        continue
                    key = (tag, von, nach, num)
                    bestaetigt.add(key)
                    zusatz[key] = {"aircraft": z["aircraft"], "times": z["times"]}
            except Exception as exc:                       # noqa: BLE001
                fehler.append(f"{direction} {tag}: {exc}")
            time.sleep(0.5)
    return bestaetigt, zusatz, ("; ".join(fehler) if fehler else "")


def split_directions(html: str) -> tuple[str, str]:
    """Trennt die Seite in Abflug- (FMO->MUC) und Ankunftsblock (MUC->FMO)."""
    out_idx = html.find('id="abflug"')
    in_idx = html.find('id="ankunft"')
    if out_idx == -1 or in_idx == -1 or in_idx < out_idx:
        raise SystemExit("Struktur der FMO-Seite hat sich geaendert: Tabs 'abflug'/'ankunft' nicht gefunden.")
    return html[out_idx:in_idx], html[in_idx:]


def parse_block(block: str, direction: str) -> list[dict]:
    flights = []
    for row in ROW_RE.findall(block):
        fields: dict[str, list[str]] = {}
        for name, fragment in FIELD_RE.findall(row):
            fields.setdefault(name, []).append(_text(fragment))
        date_m = DATE_RE.search(fields.get("time", [""])[0])
        flight_m = FLIGHT_RE.search(fields.get("status", [""])[0])
        dests = fields.get("destination", [])
        # Die FMO-Seite liefert je Tafel nur eine belastbare Zeit:
        #   Abflugtafel  -> Abflug immer, Ankunft teils "N/A"
        #   Ankunftstafel-> Ankunft immer, Abflug teils "N/A"
        # Beide Faelle muessen verwertet werden, sonst fehlen Fluege im Kalender.
        dep_m = TIME_RE.search(dests[0]) if dests else None
        arr_m = TIME_RE.search(dests[1]) if len(dests) > 1 else None
        if not (date_m and flight_m and (dep_m or arr_m)):
            continue
        day, month, year = date_m.group(1).split(".")
        airline = "Lufthansa"
        img = re.search(r'<img title="([^"]+)"', row)
        if img:
            airline = img.group(1)
        dow = fields.get("day", [""])[0].split()[-1] if fields.get("day") else ""
        flights.append({
            "date": f"{year}-{month}-{day}",
            "dow": dow,
            "dep": dep_m.group(0) if dep_m else "",
            "arr": arr_m.group(0) if arr_m else "",
            "airline": airline,
            "flight": re.sub(r"\s+", " ", flight_m.group(1)),
            "from": "FMO" if direction == "out" else "MUC",
            "to": "MUC" if direction == "out" else "FMO",
        })
    return flights


def local_to_utc(date_str: str, hhmm: str) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in date_str.split("-"))
    return dt.datetime(y, mo, d, h, m, tzinfo=TZ).astimezone(dt.timezone.utc)


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            chunks.append(cur)
            cur = b""
        cur += b
    chunks.append(cur)
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def build_ics(flights: list[dict], keep_past: bool = False,
              confirmed: set | None = None, zusatz: dict | None = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(TZ).date()
    events = []
    seen = set()

    # Typische Blockzeit aus den Fluegen mit beiden Zeiten (Median), in Minuten.
    dauern = []
    for f in flights:
        if f.get("arr") and f.get("dep"):
            a = local_to_utc(f["date"], f["dep"])
            b = local_to_utc(f["date"], f["arr"])
            m = int((b - a).total_seconds() // 60)
            if 30 <= m <= 180:
                dauern.append(m)
    dauern.sort()
    block = dauern[len(dauern) // 2] if dauern else 75

    for f in sorted(flights, key=lambda x: (x["date"], x["dep"] or x["arr"], x["flight"])):
        if not keep_past and dt.date.fromisoformat(f["date"]) < today:
            continue
        uid_key = f'{f["date"]}-{f["from"]}{f["to"]}-{f["flight"].replace(" ", "")}'
        if uid_key in seen:
            continue
        seen.add(uid_key)
        if f["dep"]:
            start = local_to_utc(f["date"], f["dep"])
        else:  # Auf der Ankunftstafel fehlt bei einem Teil der Fluege die Abflugzeit.
            start = local_to_utc(f["date"], f["arr"]) - dt.timedelta(minutes=block)
        if f["arr"]:
            end = local_to_utc(f["date"], f["arr"])
        else:
            end = start + dt.timedelta(minutes=block)
        if end <= start:                      # Ankunft nach Mitternacht
            end += dt.timedelta(days=1)
        abflug = (f'{f["dep"]} Uhr' if f["dep"] else
                  f'ca. {start.astimezone(TZ).strftime("%H:%M")} Uhr (nicht veröffentlicht, abgeleitet)')
        ankunft = (f'{f["arr"]} Uhr' if f["arr"] else
                   f'ca. {end.astimezone(TZ).strftime("%H:%M")} Uhr (nicht veröffentlicht, abgeleitet)')
        key = (f["date"], f["from"], f["to"], flight_number_key(f["flight"]))
        ist_bestaetigt = bool(confirmed) and key in confirmed
        info = (zusatz or {}).get(key, {})
        uid = f"{uid_key}@fmo-muc-schedule"
        summary = f'{f["flight"]} {f["from"]}→{f["to"]}' + (" [bestätigt]" if ist_bestaetigt else "")
        bestaetigung = ""
        if ist_bestaetigt:
            muster = (f', Flugzeugtyp laut Münchener Tafel: {info["aircraft"]}'
                      if info.get("aircraft") else "")
            bestaetigung = (f"Gegenprobe: steht auch auf der Flugtafel des Flughafens München "
                            f"(Stand {now.strftime('%d.%m.%Y')}){muster}.\\n")
        desc = (
            f'{f["airline"]} · {f["flight"]} · {f["from"]} → {f["to"]}\\n'
            f'Abflug {abflug}, Ankunft {ankunft} (jeweils Ortszeit; übliche Blockzeit {block} Minuten)\\n'
            f'Verkehrstag: {f["dow"]}\\n'
            f"{bestaetigung}"
            f"Quelle: {SOURCE} (Flughafen Münster/Osnabrück), Stand {now.strftime('%d.%m.%Y %H:%M')} UTC.\\n"
            "Planmäßiger Flug – Zeiten und Durchführung können sich ändern. Keine Buchung, keine Verfügbarkeit."
        )
        events.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{ics_escape(desc)}",
            f"LOCATION:{ics_escape('Flughafen Münster/Osnabrück (FMO)' if f['from'] == 'FMO' else 'Flughafen München (MUC)')}",
            f"CATEGORIES:{'FMO-MUC' if f['from'] == 'FMO' else 'MUC-FMO'}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hermes-agent//fmo-muc-flight-schedule//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:FMO ⇄ MUC Flugplan (Lufthansa)",
        "X-WR-CALDESC:Planmäßige Verbindungen Münster/Osnabrück (FMO) ⇄ München (MUC), beide Richtungen. Quelle: Flugplan fmo.de, täglich neu erzeugt. Termine mit [bestätigt] stehen zusätzlich auf der Flugtafel des Flughafens München. Planzeiten ohne Gewähr – keine Buchung, keine Verfügbarkeit.",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    body = [fold(line) for line in events]
    return "\r\n".join(header + body + ["END:VCALENDAR", ""])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fmo-muc.ics")
    ap.add_argument("--weeks", type=float, default=DEFAULT_WEEKS,
                    help=f"Reichweite ab morgen in Wochen (Standard {DEFAULT_WEEKS})")
    ap.add_argument("--confirm-days", type=int, default=CONFIRM_DAYS,
                    help=f"Wie viele Tage gegen die Muenchener Tafel geprueft werden (Standard {CONFIRM_DAYS})")
    ap.add_argument("--keep-past", action="store_true")
    args = ap.parse_args()

    flights = collect_flights(args.weeks)
    # Doppelte Eintraege aus angrenzenden Abschnitten entfernen
    flights = list({(f["date"], f["from"], f["to"], f["flight"]): f for f in flights}.values())

    confirmed, zusatz, hinweis = confirm_against_muc(flights, args.confirm_days)
    print(f"Gegenprobe Muenchen: {len(confirmed)} bestaetigte Fluege im "
          f"{args.confirm_days}-Tage-Fenster."
          + (f" Hinweis: {hinweis}" if hinweis else ""))

    ics = build_ics(flights, keep_past=args.keep_past, confirmed=confirmed, zusatz=zusatz)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(ics)
    days = sorted({f["date"] for f in flights})
    print(f"Fluege geparst: {len(flights)}  Zeitraum: {days[0]} .. {days[-1]}  "
          f"Events im Feed: {ics.count('BEGIN:VEVENT')}  -> {args.out} "
          f"({len(ics.encode())} bytes, sha256 {hashlib.sha256(ics.encode()).hexdigest()[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
