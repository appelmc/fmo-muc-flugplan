#!/usr/bin/env python3
"""Baut einen iCalendar-Feed (ICS) für die Lufthansa-Verbindungen FMO <-> MUC.

Quelle: server-gerenderte Flugliste auf https://www.fmo.de/muenchen/
(Zeitraum: ab heute, ca. 4 Wochen; beide Richtungen auf einer Seite).
Die Flugplandaten ändern sich saisonal -> taeglich neu erzeugen.

Aufruf:
    python3 build_fmo_ics.py [--out PATH] [--keep-past]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

SOURCE = "https://www.fmo.de/muenchen/"
UA = "Mozilla/5.0 (compatible; fmo-muc-schedule/1.0)"
TZ = ZoneInfo("Europe/Berlin")

ROW_RE = re.compile(r'<a class="[^"]*flight-list-item[^"]*"(.*?)</a>', re.S)
FIELD_RE = re.compile(r'<div class="flight-list-item-(time|day|destination|airline|status)[^"]*">(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
TIME_RE = re.compile(r"(\d{2}:\d{2})")
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
FLIGHT_RE = re.compile(r"([A-Z0-9]{2}\s?\d{3,4})")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", fragment)).strip()



def fetch(url: str = SOURCE) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


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
        times = TIME_RE.findall(" ".join(fields.get("destination", [])))
        date_m = DATE_RE.search(fields.get("time", [""])[0])
        flight_m = FLIGHT_RE.search(fields.get("status", [""])[0])
        if not date_m or not flight_m or len(times) < 2:
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
            "dep": times[0],
            "arr": times[1],
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


def build_ics(flights: list[dict], keep_past: bool = False) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(TZ).date()
    events = []
    seen = set()
    for f in sorted(flights, key=lambda x: (x["date"], x["dep"], x["flight"])):
        if not keep_past and dt.date.fromisoformat(f["date"]) < today:
            continue
        uid_key = f'{f["date"]}-{f["from"]}{f["to"]}-{f["flight"].replace(" ", "")}'
        if uid_key in seen:
            continue
        seen.add(uid_key)
        start = local_to_utc(f["date"], f["dep"])
        end = local_to_utc(f["date"], f["arr"])
        if end <= start:                      # Ankunft nach Mitternacht
            end += dt.timedelta(days=1)
        uid = f"{uid_key}@fmo-muc-schedule"
        summary = f'{f["flight"]} {f["from"]}→{f["to"]}'
        desc = (
            f'{f["airline"]} · {f["flight"]} · {f["from"]} → {f["to"]}\\n'
            f'Abflug {f["dep"]} Uhr, Ankunft {f["arr"]} Uhr (Ortszeit)\\n'
            f'Verkehrstag: {f["dow"]}\\n'
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
        "X-WR-CALDESC:Planmäßige Lufthansa-Verbindungen Münster/Osnabrück (FMO) ⇄ München (MUC). Quelle: fmo.de. Ohne Gewähr.",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    body = [fold(line) for line in events]
    return "\r\n".join(header + body + ["END:VCALENDAR", ""])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fmo-muc.ics")
    ap.add_argument("--keep-past", action="store_true")
    args = ap.parse_args()

    html = fetch()
    out_block, in_block = split_directions(html)
    flights = parse_block(out_block, "out") + parse_block(in_block, "in")
    if not flights:
        print("FEHLER: keine Fluege geparst – Seitenstruktur pruefen.", file=sys.stderr)
        return 2
    ics = build_ics(flights, keep_past=args.keep_past)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(ics)
    days = sorted({f["date"] for f in flights})
    print(f"Fluege geparst: {len(flights)}  Zeitraum: {days[0]} .. {days[-1]}  "
          f"Events im Feed: {ics.count('BEGIN:VEVENT')}  -> {args.out} "
          f"({len(ics.encode())} bytes, sha256 {hashlib.sha256(ics.encode()).hexdigest()[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
