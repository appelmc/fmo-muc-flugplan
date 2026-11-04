#!/usr/bin/env python3
"""Selbsttest fuer build_fmo_ics.py - laeuft ohne Netz.

Schwerpunkt: Robustheit gegen die Wartungsplatzseite von fmo.de. Seit Anfang
November 2026 liefert die Quelle unter derselben Adresse abwechselnd die echte
Flugplan-Anwendung und eine Wartungsplatzseite (HTTP 200, ~16 kB, ohne
id="abflug"/"ankunft"). Frueher riss ein einzelner solcher Treffer den ganzen
Lauf ab: split_directions() warf SystemExit (BaseException), das der
`except Exception` in collect_flights() nicht fangen konnte.

Geprueft wird hier:
  * Erkennung der Wartungsseite (echte Seite, echter Wartungsauszug als Fixture),
  * split_directions() wirft kein SystemExit mehr,
  * fetch_page() wiederholt GET und POST nach Pause,
  * ein Wartungstreffer beendet den Lauf nicht, die uebrigen Monate laufen weiter,
  * fehlende Flugplan-Struktur bleibt ein klarer Fehler (kein stilles Nichts).

Aufruf:  python3 test_wartung.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import pathlib
import re
import sys
import types

HIER = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fmo", HIER / "build_fmo_ics.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

WARTUNG = (HIER / "tests/fixtures/fmo_wartungsseite.html").read_text(encoding="utf-8")
FLUGPLAN = (HIER / "tests/fixtures/fmo_flugplan_ausschnitt.html").read_text(encoding="utf-8")
TAFEL = ('<html><body><div id="abflug"><div class="departureToday"></div></div>'
         '<div id="ankunft"><div class="arrivalToday"></div></div></body></html>')

HEUTE = dt.datetime.now(m.TZ).date()
MORGEN = HEUTE + dt.timedelta(days=1)
ZIELMONAT = f"{MORGEN.year}-{MORGEN.month:02d}"


def ohne_pausen() -> list[float]:
    """time.sleep durch Zaehler ersetzen, damit der Test schnell bleibt."""
    pausen: list[float] = []
    m.time = types.SimpleNamespace(sleep=pausen.append)
    return pausen


# --- 1. Erkennung der Wartungsseite -----------------------------------------
assert "Wartungsarbeiten" in WARTUNG, "Fixture ist keine Wartungsseite"
assert m.ist_wartungsseite(WARTUNG), "Wartungsseite nicht erkannt"
assert not m.ist_wartungsseite(FLUGPLAN), "echte Flugplanseite falsch verdaechtigt"
assert m.ist_wartungsseite("<html><body>kein Hinweis, keine Anker</body></html>"), \
    "Seite ohne Flugplan-Anker nicht erkannt"
print("OK: Wartungsseite erkannt, echte Seite und Flugplan nicht falsch verdaechtigt.")

# --- 2. split_directions wirft kein SystemExit mehr -------------------------
STRUKTURSEITE = ("<html><body>" + "x" * 50_000 + "</body></html>")   # gross, ohne Anker
faelle = (
    (WARTUNG, "Wartungsseite", m.Wartungsseite),
    ("<html><body>kleine Platzhalterseite ohne Anker</body></html>", "kleine Fremdseite", m.Wartungsseite),
    (STRUKTURSEITE, "grosse Seite ohne Anker", RuntimeError),
)
for seite, name, erwartet in faelle:
    if erwartet is m.Wartungsseite:
        assert m.ist_wartungsseite(seite), f"{name} nicht als Wartung erkannt"
    else:
        assert not m.ist_wartungsseite(seite), f"{name} faelschlich als Wartung gewertet"
    try:
        m.split_directions(seite)
    except SystemExit as exc:                                  # darf nicht passieren
        raise AssertionError(f"{name}: split_directions wirft weiter SystemExit: {exc}")
    except RuntimeError as exc:
        assert isinstance(exc, erwartet), f"{name}: {type(exc).__name__} statt {erwartet.__name__}"
        print(f"OK: {name} -> {type(exc).__name__} ({str(exc)[:90]})")
    else:
        raise AssertionError(f"{name}: split_directions haette fehlschlagen muessen")

# --- 3. fetch_page wiederholt nach Pause ------------------------------------
pausen = ohne_pausen()
aufrufe = {"n": 0}


def nur_wartung(url, data):
    aufrufe["n"] += 1
    return WARTUNG


echtes_abrufen = m._abrufen
m._abrufen = nur_wartung
try:
    m.fetch_page(m.SOURCE)
except m.Wartungsseite as exc:
    print(f"OK: nach {m.FMO_VERSUCHE} Versuchen Wartungsseite gemeldet: {exc}")
else:
    raise AssertionError("fetch_page haette Wartungsseite melden muessen")
assert aufrufe["n"] == m.FMO_VERSUCHE, f"erwartet {m.FMO_VERSUCHE} Versuche, waren {aufrufe['n']}"
assert len(pausen) == m.FMO_VERSUCHE - 1, f"erwartet {m.FMO_VERSUCHE - 1} Pausen, waren {len(pausen)}"
assert all(p == m.FMO_PAUSE for p in pausen), f"Pausendauer falsch: {pausen}"
print(f"OK: {aufrufe['n']} Versuche, {len(pausen)} Pause(n) a {m.FMO_PAUSE:.0f} s.")

# --- 4. Wiederholung rettet einen flatternden Abruf (GET und POST) ----------
for beschriftung, daten in (("GET", None),
                            ("POST", {"doFlightSearch[destination]": "MUC"})):
    pausen = ohne_pausen()
    versuche = {"n": 0}

    def flattert(url, data, _v=versuche):
        _v["n"] += 1
        return WARTUNG if _v["n"] < m.FMO_VERSUCHE else FLUGPLAN

    m._abrufen = flattert
    seite = m.fetch_page(m.SOURCE, daten)
    assert seite == FLUGPLAN, f"{beschriftung}: echte Seite nicht durchgekommen"
    assert versuche["n"] == m.FMO_VERSUCHE and len(pausen) == m.FMO_VERSUCHE - 1
    print(f"OK: {beschriftung} - {versuche['n']} Versuche, echter Inhalt geliefert.")

m._abrufen = echtes_abrufen

# --- 5. Wartungstreffer beendet den Lauf nicht ------------------------------
# Ein Monatsabschnitt liefert nur die Wartungsseite, alle uebrigen die echte
# Flugplanseite. Frueher brach hier der ganze Lauf ab (SystemExit).
pausen = ohne_pausen()
getroffen: list[str] = []
abgefragt: list[str] = []


def seite_fuer(von: str) -> str:
    """Flugplan-Fixture mit den Tagen des angefragten Monats."""
    tag = dt.date.fromisoformat(von)
    return re.sub(r"(\d{2})\.(\d{2})\.(\d{4})",
                  lambda t: f"{t.group(1)}.{tag.month:02d}.{tag.year}", FLUGPLAN)


def quelle(url, data=None, versuche=None):
    if url == m.BOARD:
        return TAFEL
    if data is None:
        return FLUGPLAN
    von = str(data["doFlightSearch[dateFrom]"])
    abgefragt.append(von)
    if von[:7] == ZIELMONAT:
        getroffen.append(von)
        return WARTUNG
    return seite_fuer(von)


m.fetch_page = quelle
stderr = io.StringIO()
with contextlib.redirect_stderr(stderr):
    flights = m.collect_flights(weeks=12)         # 3 Monatsabschnitte

assert getroffen, "Zielmonat wurde im Test nicht getroffen"
assert flights, "Lauf lieferte trotz Wartungstreffer keine Fluege"
monate = sorted({f["date"][:7] for f in flights})
assert len(abgefragt) >= 3, f"weitere Monate wurden nicht mehr abgefragt: {abgefragt}"
assert ZIELMONAT not in monate, f"Wartungsmonat {ZIELMONAT} hat trotzdem Fluege geliefert"
assert len(monate) >= 2, f"uebrige Monate nicht weiterverarbeitet: {monate}"
warnung = stderr.getvalue()
assert "Wartungsseite" in warnung, f"Ursache nicht benannt: {warnung!r}"
assert ZIELMONAT in warnung, f"betroffener Abschnitt nicht benannt: {warnung!r}"
print(f"OK: Wartungstreffer in {ZIELMONAT} beendet den Lauf nicht - "
      f"{len(abgefragt)} Monatsabschnitte abgefragt, {len(flights)} Fluege aus "
      f"{len(monate)} Monaten {monate} weiterverarbeitet.")
zeilen = [z for z in warnung.strip().splitlines() if "Monatsabschnitten" in z]
print(f"    Ursachenzeile: {(zeilen[0] if zeilen else warnung.strip())[:200]}")

# --- 6. Dauerhafte Wartung: klarer Abbruch mit Ursache, Tafel inbegriffen ---
def nur_wartung_seite(url, data=None, versuche=None):
    return WARTUNG


m.fetch_page = nur_wartung_seite
ohne_pausen()
stderr = io.StringIO()
try:
    with contextlib.redirect_stderr(stderr):
        m.collect_flights(weeks=12)
except SystemExit as exc:
    assert "Wartungsseite" in str(exc), f"Ursache nicht benannt: {exc}"
    assert "Struktur" not in str(exc), f"falsche Ursache (Struktur statt Wartung): {exc}"
    assert "Tages-Tafel" in stderr.getvalue() and "Wartungsseite" in stderr.getvalue(), \
        f"Tages-Tafel ohne benannte Ursache: {stderr.getvalue()!r}"
    print(f"OK: Dauerwartung -> klarer Abbruch mit Ursache: {str(exc)[:150]}")
else:
    raise AssertionError("Dauerwartung haette abbrechen muessen (kein stiller Leerlauf)")

# --- 7. Ohne jedes Flugplan-Geruest bleibt es ein klarer Fehler -------------
def fremdseite(url, data=None, versuche=None):
    return STRUKTURSEITE


m.fetch_page = fremdseite
ohne_pausen()
stderr = io.StringIO()
try:
    with contextlib.redirect_stderr(stderr):
        m.collect_flights(weeks=8)
except SystemExit as exc:
    assert "Struktur" in str(exc) and "abflug" in str(exc), f"unklare Ursache: {exc}"
    print(f"OK: fremde Struktur bleibt ein klarer Abbruch: {str(exc)[:150]}")
except Exception as exc:                                       # noqa: BLE001
    raise AssertionError(f"unerwarteter Fehlertyp {type(exc).__name__}: {exc}")
else:
    raise AssertionError("fremde Struktur haette abbrechen muessen")

# --- 8. Parser unveraendert: echter Ausschnitt ------------------------------
out_block, in_block = m.split_directions(FLUGPLAN)
parsed = m.parse_block(out_block, "out") + m.parse_block(in_block, "in")
assert len(parsed) == 6, f"Parser liest den Ausschnitt nicht mehr: {len(parsed)}"
assert {f["from"] for f in parsed} == {"FMO", "MUC"}, "Richtungen fehlen"
print("OK: Parser liest den echten Ausschnitt weiterhin korrekt (6 Fluege, beide Richtungen).")

print("\nAlle Tests bestanden.")
