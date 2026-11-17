# Daten-Job: Wolken und Wind

Stündlicher Job für die Radar-Ebenen „Wolken“ und „Wind“ der App „Schönes Wetter“.

Er holt die jüngsten Läufe des DWD-Modells **ICON-D2-RUC** (Ersatz: ICON-D2) von `opendata.dwd.de/weather/nwp/v1`,
rechnet Bewölkung, Wolkendicke und Wind in 10 m Höhe auf das Kartengitter der App um und veröffentlicht je Stunde
zwei kleine PNG-Dateien und eine Übersicht `index.json` über GitHub Pages:
<https://luigibaumigi.github.io/schoenes-wetter-daten/index.json>

**Datenbasis: Deutscher Wetterdienst.**

## Wo was liegt

| Datei | Inhalt |
|---|---|
| `daten_job.py` | Ablauf: Läufe finden, Stunden planen, abrufen, umrechnen, schreiben |
| `grib.py` | Kleiner GRIB2-Leser für das ICON-Dreiecksgitter (CCSDS-Packung über `imagecodecs`) |
| `gitter.py` | Zielgitter wie `Radarraster` der App, Umrechnung mit den vier nächsten Modellzellen |
| `tests/` | Tests mit einer echten DWD-Datei und aufgezeichneten Verzeichnisseiten, ohne Netz |
| `.github/workflows/daten-job.yml` | Zeitplan, Veröffentlichung, Meldung bei Störung (gilt im Daten-Repository) |
| `uebertragen.sh` | Überträgt diesen Ordner ins öffentliche Daten-Repository |

Das Dateiformat für die App steht im App-Repository in `docs/ENTSCHEIDUNGEN.md` („Etappe 7, Sitzung A“)
und kurz in jeder `index.json`.

## Befehle

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v     # Tests
python daten_job.py --ausgabe seite          # einmal laufen lassen, Ergebnis im Ordner seite/
```
