# FMO ⇄ MUC Flugplan als Kalender

Öffentlicher iCalendar-Feed (`.ics`) mit den planmäßigen Verbindungen
zwischen Münster/Osnabrück (FMO) und München (MUC) – beide Richtungen.

- **Quelle:** Flugplan des Flughafens Münster/Osnabrück,
  <https://www.fmo.de/muenchen/>. Die Seite zeigt nur vier Wochen; die
  Flugsuche derselben Seite wird per POST mit einem frei gewählten Zeitraum
  abgefragt und liefert rund ein Jahr voraus.
- **Inhalt:** Flugnummer, Marke (Lufthansa / Lufthansa City Airlines),
  Abflug- und Ankunftszeit, Verkehrstag
- **Reichweite:** maximales Fenster, ab heute rund ein Jahr (änderbar über `--weeks N`).
  Je weiter voraus, desto unsicherer – es ist überall dieselbe Planungsquelle,
  deshalb wird der lange Teil nicht abgeschnitten.
- **Heute und gestern:** Den heutigen Tag liefert die Tages-Tafel
  (`fmo.de/abflug-ankunft/`) – die Zeitsuche kennt ihn nicht. Der Vortag bleibt
  zusätzlich einen Tag im Feed, damit der Kalender morgens kein Loch bekommt und
  erst danach ausläuft.
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
| `test_wartung.py` | Selbsttest für die Wartungs-Erkennung und die Wiederholung – läuft ohne Netz, nutzt die Belege in `tests/fixtures/` |
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

## Wenn die Quelle in Wartung ist (Wartungsplatzseite statt Flugplan)

Seit Anfang November 2026 ist `fmo.de` im Umbau. Unter derselben Adresse
antworten abwechselnd zwei Systeme: die echte Flugplan-Anwendung und eine
**Wartungsplatzseite** (HTTP 200, rund 16 kB, „Aktuell werden geplante
Wartungsarbeiten durchgeführt … FMO.de steht Ihnen in Kürze wieder im gewohnten
Umfang zur Verfügung"). Betroffen sind GET **und** POST, am 04.11.2026 gemessen
im Verhältnis grob 2 echte zu 3 Wartungsantworten.

Darauf ist der Generator eingestellt:

- **Erkennung:** Hinweistext der Platzhalterseite; zusätzlich gilt eine kleine
  Antwort ohne die Anker `id="abflug"`/`id="ankunft"` als Wartungsseite.
- **Wiederholung:** Jeder Abruf wird bis zu **3-mal** versucht, dazwischen
  **30 s** Pause (`FMO_VERSUCHE`, `FMO_PAUSE`). Erst danach gilt er als Fehler –
  und dann mit klarer Ursache („Wartungsseite statt Flugplan …"), nicht mit
  „Struktur hat sich geändert".
- **Teilausfall bricht den Lauf nicht ab:** Das gilt auch für die Tages-Tafel und
  die Gegenprobe gegen München. Ein betroffener Monatsabschnitt landet in der
  Fehlerliste, die übrigen Monate laufen weiter; die Warnzeile nennt Anzahl und
  Abschnitte.
- **Nichts wird still überschrieben:** Kommt gar nichts Echtes zurück, endet der
  Lauf mit Fehler, bevor eine Datei geschrieben wird – der letzte gute Feed
  bleibt stehen. Dasselbe erreicht die Mindestzahl-Prüfung im Workflow.
- **Keine Lücke bei Teilausfall:** Fällt ein Monatsabschnitt aus, holt der Schritt
  „Vortag übernehmen" die fehlenden Termine aus dem bisherigen Feed zurück
  (alles ab gestern, was im neuen Feed fehlt). Der veröffentlichte Kalender
  verliert dadurch keinen Monat, solange den Lauf mindestens ein Abschnitt
  gelingt. Am 04.11.2026 real geprüft: 7 von 13 Abschnitten fielen aus, der
  erzeugte Feed hatte 1.213 Termine, nach der Übernahme 2.552.

Der Parser selbst ist unverändert: Die neue Seite ist strukturgleich, die
Abflug-/Ankunftsblöcke und das POST-Formular funktionieren wie vorher.

Prüfen ohne Netz (Belege in `tests/fixtures/`, echter Wartungsauszug und ein
echter Seitenausschnitt):

```bash
python3 test_wartung.py
```

## Hinweis zur Quelle

Nicht jede Zeile trägt beide Uhrzeiten: Auf der Abflugtafel fehlt bei einem
Teil der Flüge die Ankunft (`Ankunft N/A`), auf der Ankunftstafel umgekehrt der
Abflug. Der Generator verwirft solche Zeilen nicht, sondern leitet die fehlende
Zeit aus der üblichen Blockzeit ab und kennzeichnet sie im Termin als
abgeleitet. Ändert der Flughafen seine Seitenstruktur, bricht der Lauf mit
Fehler ab, statt einen leeren Kalender zu veröffentlichen. Die Wartungsphase
wird davon unterschieden: sie wird zuerst durch Wiederholung überbrückt und
erst dann – mit eigener Ursachenzeile – als Fehler gemeldet.
