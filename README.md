# FMO ⇄ MUC Flugplan als Kalender

Öffentlicher iCalendar-Feed (`.ics`) mit den planmäßigen Verbindungen
zwischen Münster/Osnabrück (FMO) und München (MUC) – beide Richtungen.

- **Quelle:** Flugplan des Flughafens Münster/Osnabrück,
  <https://www.fmo.de/muenchen/>. Die Seite zeigt nur vier Wochen; die
  Flugsuche derselben Seite wird per POST mit einem frei gewählten Zeitraum
  abgefragt und liefert rund ein Jahr voraus.
- **Inhalt:** Flugnummer, Marke (Lufthansa / Lufthansa City Airlines),
  Abflug- und Ankunftszeit, Verkehrstag
- **Reichweite:** maximales Fenster, ab morgen rund ein Jahr (änderbar über `--weeks N`).
  Je weiter voraus, desto unsicherer – es ist überall dieselbe Planungsquelle,
  deshalb wird der lange Teil nicht abgeschnitten.
- **Bestätigungsstufe:** Die ersten 14 Tage werden zusätzlich gegen die
  **Flugtafel des Flughafens München** geprüft (`munich-airport.com`). Flüge,
  die dort für denselben Tag stehen, tragen im Titel ein ✅; alle anderen
  bleiben unmarkiert. Den Flugzeugtyp nennt die Tafel nur für den nächsten Tag.
- **Aktualisierung:** täglich per GitHub Actions (`04:17 UTC`), Datei `fmo-muc.ics`
- **Ohne Gewähr:** planmäßige Zeiten, keine Buchung, keine Verfügbarkeit.
  Weit in der Zukunft liegende Einträge sind der heutige Plan und ändern sich.

## Abonnieren (iPhone / iPad)

Einstellungen → Kalender → Accounts → Account hinzufügen → Andere →
**Abonnierten Kalender hinzufügen** → HTTPS-URL eintragen:

```
https://<BENUTZERNAME>.github.io/<REPO>/fmo-muc.ics
```

## Aufbau

| Datei | Zweck |
|---|---|
| `build_fmo_ics.py` | Holt den Flugplan (POST, monatsweise), parst beide Richtungen, gleicht die ersten 14 Tage gegen Lufthansa ab, schreibt das ICS – nur Python-Standardbibliothek |
| `test_bestaetigung.py` | Selbsttest für XML-Parser und `[bestätigt]`-Markierung – läuft ohne Zugangsdaten |
| `.github/workflows/update-feed.yml` | Erzeugt den Feed täglich neu und committet ihn |
| `fmo-muc.ics` | Der erzeugte Kalender – wird vom Workflow geschrieben, nicht von Hand gepflegt |

Lokal ausprobieren:

```bash
python3 build_fmo_ics.py --out fmo-muc.ics               # Standard: maximales Fenster
python3 build_fmo_ics.py --weeks 8 --out fmo-muc.ics     # nur acht Wochen
python3 build_fmo_ics.py --confirm-days 7 --out fmo-muc.ics  # kürzerer Airline-Abgleich
```

## Bestätigungsstufe (Flugtafel des Flughafens München)

Die ersten 14 Tage werden gegen die Tagesansicht der Münchener Flugtafel
geprüft — ein eigenes System, das aus dem Flugverkehr gespeist wird. Trifft der
Flug dort für denselben Tag zu, steht ein **✅** am Ende des Termintitels:

```
LH 2143 FMO→MUC ✅
```

Das Zeichen ist frei wählbar, ohne den Code anzufassen:

```bash
python3 build_fmo_ics.py --marker "✓"      # schlichtes Häkchen
python3 build_fmo_ics.py --marker "🟢"     # grüner Punkt
python3 build_fmo_ics.py --marker " [LH]"  # wieder Text
```

Den Flugzeugtyp nennt die Tafel nur für den **nächsten Tag** (und dort nur auf
der Abflugtafel); weiter voraus steht in den Terminen daher kein Muster.

Keine Zugangsdaten, kein Konto. Zwei Eigenheiten der Tafel sind im Generator
berücksichtigt: sie verlangt ein Cookie von der Startseite (ohne das antwortet
der Tages-Endpunkt mit HTTP 500), und sie gibt nur **einen Tag je Abruf**
heraus — für 14 Tage sind das 28 Abrufe, Laufzeit rund eine Minute.

Der Abgleich läuft gegen die Flugnummer, nicht gegen den Markennamen: dieselbe
Rotation heißt im Winter `LH`, im Winter 2026/27 teils `VL` (Lufthansa City
Airlines). Bei einem Fehler bleibt die Markierung aus, es wird nie ein Flug
fälschlich als bestätigt ausgegeben. Abschaltbar mit `--confirm-days 0`.

Die Lufthansa Open API wäre die Airline selbst, ist aber derzeit nicht
nutzbar: „Registration to OpenAPI is on hold until further notice"
(developer.lufthansa.com, Stand 07.10.2026).

## Hinweis zur Quelle

Nicht jede Zeile trägt beide Uhrzeiten: Auf der Abflugtafel fehlt bei einem
Teil der Flüge die Ankunft (`Ankunft N/A`), auf der Ankunftstafel umgekehrt der
Abflug. Der Generator verwirft solche Zeilen nicht, sondern leitet die fehlende
Zeit aus der üblichen Blockzeit ab und kennzeichnet sie im Termin als
abgeleitet. Ändert der Flughafen seine Seitenstruktur, bricht der Lauf mit
Fehler ab, statt einen leeren Kalender zu veröffentlichen.
