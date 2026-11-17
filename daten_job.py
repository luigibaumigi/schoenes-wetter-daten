"""Daten-Job für die Radar-Ebenen Wolken und Wind.

Holt die jüngsten Läufe des DWD-Modells ICON-D2-RUC (Ersatz: ICON-D2) von opendata.dwd.de,
rechnet Bewölkung, Wolkendicke und Wind auf das Kartengitter der App um und schreibt je Stunde
eine kleine PNG-Datei je Ebene plus eine Übersicht `index.json`. Das Dateiformat steht in
docs/ENTSCHEIDUNGEN.md („Etappe 7, Sitzung A“).

Aufruf:  python daten_job.py --ausgabe <ordner>
Scheitert etwas, endet das Skript mit Fehlercode 1 und einer Meldung auf Deutsch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitter  # noqa: E402
import grib  # noqa: E402

BASIS = "https://opendata.dwd.de/weather/nwp/v1/m"
KENNUNG = "schoenes-wetter-daten-job/1"
QUELLE = "Datenbasis: Deutscher Wetterdienst"
FORMAT = 1


@dataclass(frozen=True)
class Modell:
    ordner: str
    name: str
    laeufe_stunden: int  # Abstand zweier Läufe
    max_vorlauf: int  # so weit voraus wird ausgeliefert (Stunden)


MODELLE = [
    Modell("icon-d2-ruc", "ICON-D2-RUC", 1, 8),
    Modell("icon-d2", "ICON-D2", 3, 12),
]

# Ausschnitt wie das Deutschland-Raster des Regenradars (Radarverbund des DWD): 1 bis 19° Ost, 45 bis 57° Nord.
WOLKEN_GITTER = gitter.quadratisch(1.0, 45.0, 19.0, 57.0, 540)  # 1/30° ≈ 2,3 km je Bildpunkt, so fein wie das Modell
WIND_GITTER = gitter.quadratisch(1.0, 45.0, 19.0, 57.0, 180)  # 0,1° ≈ 7 km je Bildpunkt

VARIABLEN = ["CLCT", "TQC", "TQI", "U_10M", "V_10M"]
STUNDEN_ZURUECK = 3  # Zeitleiste: 2 Stunden zurück, eine Stunde Reserve
LAUF_HOECHSTENS_ALT = dt.timedelta(hours=5)  # älter: DWD liefert nicht, der Job meldet einen Fehler

WOLKEN_STUFEN = 63  # 64 Abstufungen je Kanal: sieht man nicht, spart ein Drittel der Dateigröße
WIND_NULL = 128  # Kodierung: Byte = 128 + 3 · (m/s), gerundet, 1 bis 255
WIND_SCHRITTE_JE_MS = 3


class JobFehler(Exception):
    pass


# ---------- Abruf ----------

def hole(url: str, versuche: int = 4) -> bytes:
    letzter: Exception | None = None
    for versuch in range(versuche):
        try:
            anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG})
            with urllib.request.urlopen(anfrage, timeout=60) as antwort:
                return antwort.read()
        except urllib.error.HTTPError as fehler:
            if fehler.code == 404:
                raise
            letzter = fehler
        except (urllib.error.URLError, TimeoutError, ConnectionError) as fehler:
            letzter = fehler
        time.sleep(2 + 4 * versuch)
    raise JobFehler(f"Abruf gescheitert nach {versuche} Versuchen: {url} ({letzter})")


_LISTE = re.compile(r'href="([^"?/][^"]*)"')


def liste(url: str) -> list[str]:
    """Einträge einer Verzeichnisseite von opendata.dwd.de (ohne „../“)."""
    try:
        html = hole(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as fehler:
        if fehler.code == 404:
            return []
        raise
    return [urllib.parse.unquote(e).rstrip("/") for e in _LISTE.findall(html) if not e.startswith("..")]


def laeufe(modell: Modell, variable: str) -> list[dt.datetime]:
    zeiten = []
    for eintrag in liste(f"{BASIS}/{modell.ordner}/p/{variable}/r/"):
        try:
            zeiten.append(dt.datetime.strptime(eintrag, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc))
        except ValueError:
            continue
    return sorted(zeiten)


def lauf_ordner(lauf: dt.datetime) -> str:
    return urllib.parse.quote(lauf.strftime("%Y-%m-%dT%H:%M"))


def schritt_datei(vorlauf_stunden: int) -> str:
    return f"PT{vorlauf_stunden:03d}H00M.grib2"


def vorhandene_schritte(modell: Modell, variable: str, lauf: dt.datetime) -> set[int]:
    stunden = set()
    for eintrag in liste(f"{BASIS}/{modell.ordner}/p/{variable}/r/{lauf_ordner(lauf)}/s/"):
        treffer = re.fullmatch(r"PT(\d{3})H00M\.grib2", eintrag)
        if treffer:
            stunden.add(int(treffer.group(1)))
    return stunden


# ---------- Planung: welche Stunde aus welchem Lauf ----------

@dataclass(frozen=True)
class Bild:
    zeit: dt.datetime
    lauf: dt.datetime
    modell: Modell

    @property
    def vorlauf(self) -> int:
        return int((self.zeit - self.lauf).total_seconds() // 3600)


def plane(jetzt: dt.datetime) -> tuple[Modell, dt.datetime, list[Bild]]:
    """Sucht den jüngsten vollständigen Lauf und legt fest, aus welchem Lauf jede Stunde kommt.

    Vergangene Stunden kommen aus dem Lauf, der zu dieser Stunde gestartet ist (Vorlauf 0, die beste
    Schätzung des Modells für diesen Zeitpunkt), sofern es ihn gibt; sonst aus dem jüngeren Lauf davor.
    """
    start = jetzt.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=STUNDEN_ZURUECK)
    gruende = []
    for modell in MODELLE:
        alle = laeufe(modell, "CLCT")
        if not alle:
            gruende.append(f"{modell.name}: keine Läufe gefunden")
            continue
        schritte_cache: dict[tuple[str, dt.datetime], set[int]] = {}

        def hat(variable: str, lauf: dt.datetime, vorlauf: int) -> bool:
            schluessel = (variable, lauf)
            if schluessel not in schritte_cache:
                schritte_cache[schluessel] = vorhandene_schritte(modell, variable, lauf)
            return vorlauf in schritte_cache[schluessel]

        def vollstaendig(lauf: dt.datetime, vorlauf: int) -> bool:
            return all(hat(v, lauf, vorlauf) for v in VARIABLEN)

        # jüngster Lauf, der alle Stunden bis zum Ende seiner Auslieferung hat
        neuester = None
        for lauf in reversed(alle[-4:]):
            if all(vollstaendig(lauf, h) for h in range(0, modell.max_vorlauf + 1)):
                neuester = lauf
                break
        if neuester is None:
            gruende.append(f"{modell.name}: keiner der letzten vier Läufe vollständig")
            continue
        if jetzt - neuester > LAUF_HOECHSTENS_ALT:
            gruende.append(f"{modell.name}: jüngster vollständiger Lauf {neuester:%d.%m. %H:%M} UTC ist zu alt")
            continue

        bilder = []
        zeit = start
        ende = neuester + dt.timedelta(hours=modell.max_vorlauf)
        while zeit <= ende:
            if zeit >= neuester:
                bilder.append(Bild(zeit, neuester, modell))
            else:
                passend = next((lauf for lauf in reversed(alle)
                                if lauf <= zeit and vollstaendig(lauf, int((zeit - lauf).total_seconds() // 3600))), None)
                if passend is not None:
                    bilder.append(Bild(zeit, passend, modell))
            zeit += dt.timedelta(hours=1)
        return modell, neuester, bilder
    raise JobFehler("Kein brauchbarer Modelllauf: " + "; ".join(gruende))


# ---------- Umrechnen und Schreiben ----------

def feld(modell: Modell, variable: str, lauf: dt.datetime, vorlauf: int) -> grib.Feld:
    url = f"{BASIS}/{modell.ordner}/p/{variable}/r/{lauf_ordner(lauf)}/s/{schritt_datei(vorlauf)}"
    try:
        return grib.lies(hole(url))
    except grib.GribFehler as fehler:
        raise JobFehler(f"{variable} {lauf:%d.%m. %H:%M}+{vorlauf} h: {fehler}") from fehler


def koordinaten(modell: Modell, laeufe_kandidaten: list[dt.datetime]) -> tuple[grib.Feld, grib.Feld]:
    """Mittelpunkte der ICON-Zellen. Der DWD legt sie nicht jedem Lauf bei; der jüngste mit Datei gewinnt."""
    for lauf in sorted(set(laeufe_kandidaten), reverse=True):
        if 0 in vorhandene_schritte(modell, "CLAT", lauf) and 0 in vorhandene_schritte(modell, "CLON", lauf):
            return feld(modell, "CLAT", lauf, 0), feld(modell, "CLON", lauf, 0)
    for lauf in reversed(laeufe(modell, "CLAT")):
        if 0 in vorhandene_schritte(modell, "CLON", lauf):
            return feld(modell, "CLAT", lauf, 0), feld(modell, "CLON", lauf, 0)
    raise JobFehler(f"{modell.name}: keine Koordinaten (CLAT/CLON) gefunden")


def wolkendicke(tqc: np.ndarray, tqi: np.ndarray) -> np.ndarray:
    """0 bis 1, wie hell die Wolke von oben aussähe (Albedo-Näherung aus der optischen Dicke).

    Optische Dicke τ ≈ 3·W / (2·ρ·r): Wasser mit r = 10 µm ergibt 150 je kg/m², Eis mit r = 30 µm etwa 55.
    Albedo ≈ τ / (τ + 13) (Zwei-Strom-Näherung mit Asymmetrie 0,85).
    """
    tau = 150.0 * np.nan_to_num(tqc) + 55.0 * np.nan_to_num(tqi)
    return tau / (tau + 13.0)


def wolkenbild(clct: np.ndarray, tqc: np.ndarray, tqi: np.ndarray) -> np.ndarray:
    gueltig = ~np.isnan(clct)
    bild = np.zeros(clct.shape + (3,), dtype=np.uint8)
    stufe = lambda x: np.round(np.round(np.clip(x, 0, 1) * WOLKEN_STUFEN) * 255 / WOLKEN_STUFEN).astype(np.uint8)
    bild[..., 0] = stufe(np.nan_to_num(clct) / 100)
    bild[..., 1] = stufe(wolkendicke(tqc, tqi))
    bild[..., 2] = np.where(gueltig, 255, 0)
    bild[~gueltig, :2] = 0
    return bild


def windbild(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    gueltig = ~(np.isnan(u) | np.isnan(v))
    kodiere = lambda w: np.clip(np.round(WIND_NULL + WIND_SCHRITTE_JE_MS * np.nan_to_num(w)), 1, 255).astype(np.uint8)
    bild = np.stack([kodiere(u), kodiere(v), np.where(gueltig, 255, 0).astype(np.uint8)], axis=-1)
    bild[~gueltig, :2] = WIND_NULL
    return bild


def speichere_png(bild: np.ndarray, pfad: Path) -> int:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(bild, "RGB").save(pfad, format="PNG", optimize=True)
    return pfad.stat().st_size


def iso(zeit: dt.datetime) -> str:
    return zeit.strftime("%Y-%m-%dT%H:%M:%SZ")


def kurz(zeit: dt.datetime) -> str:
    return zeit.strftime("%Y%m%dT%H%MZ")


def lauf_job(ausgabe: Path, jetzt: dt.datetime | None = None) -> dict:
    jetzt = jetzt or dt.datetime.now(dt.timezone.utc)
    beginn = time.monotonic()
    modell, neuester, bilder = plane(jetzt)
    if not bilder:
        raise JobFehler("Planung ergab keine Stunden")

    lat, lon = koordinaten(modell, [neuester] + [b.lauf for b in bilder])
    if lat.gitter_uuid != lon.gitter_uuid:
        raise JobFehler("CLAT und CLON gehören zu verschiedenen Gittern")
    wolken_umr = gitter.umrechnung(WOLKEN_GITTER, lon.werte, lat.werte)
    wind_umr = gitter.umrechnung(WIND_GITTER, lon.werte, lat.werte, max_abstand_km=6.0)

    ausgabe.mkdir(parents=True, exist_ok=True)
    eintraege = []
    bytes_gesamt = 0
    for bild in bilder:
        f = {v: feld(modell, v, bild.lauf, bild.vorlauf) for v in VARIABLEN}
        for name, fx in f.items():
            if fx.gitter_uuid != lat.gitter_uuid:
                raise JobFehler(f"{name} liegt auf einem anderen Gitter als die Koordinaten")
        w = wolkenbild(*(wolken_umr.anwenden(f[v].werte) for v in ("CLCT", "TQC", "TQI")))
        wi = windbild(wind_umr.anwenden(f["U_10M"].werte), wind_umr.anwenden(f["V_10M"].werte))
        name = f"{kurz(bild.zeit)}_{kurz(bild.lauf)}.png"
        bytes_gesamt += speichere_png(w, ausgabe / "wolken" / name)
        bytes_gesamt += speichere_png(wi, ausgabe / "wind" / name)
        eintraege.append({
            "zeit": iso(bild.zeit),
            "lauf": iso(bild.lauf),
            "vorlaufStunden": bild.vorlauf,
            "wolken": f"wolken/{name}",
            "wind": f"wind/{name}",
        })
        print(f"  {bild.zeit:%d.%m. %H:%M} UTC aus Lauf {bild.lauf:%H:%M} (+{bild.vorlauf} h)", flush=True)

    index = {
        "format": FORMAT,
        "erzeugt": iso(dt.datetime.now(dt.timezone.utc).replace(microsecond=0)),
        "quelle": QUELLE,
        "modell": modell.name,
        "neuesterLauf": iso(neuester),
        "wolken": {
            "gitter": WOLKEN_GITTER.als_json(),
            "rot": "Bedeckung (Gesamtbewölkung) 0 bis 1: Wert / 255, in 64 Stufen",
            "gruen": "Wolkendicke 0 bis 1: Wert / 255, in 64 Stufen (wie hell die Wolke von oben aussähe)",
            "blau": "255 = Modellwert vorhanden, 0 = außerhalb des Modellgebiets",
        },
        "wind": {
            "gitter": WIND_GITTER.als_json(),
            "hoehe": "10 m über Grund",
            "rot": "Ostwärts-Komponente u in m/s: (Wert - 128) / 3",
            "gruen": "Nordwärts-Komponente v in m/s: (Wert - 128) / 3",
            "blau": "255 = Modellwert vorhanden, 0 = außerhalb des Modellgebiets",
        },
        "zeiten": eintraege,
    }
    (ausgabe / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    dauer = time.monotonic() - beginn
    print(f"Fertig: {len(eintraege)} Stunden aus {modell.name}, jüngster Lauf {neuester:%d.%m. %H:%M} UTC, "
          f"{bytes_gesamt / 1e6:.2f} MB in {dauer:.0f} s", flush=True)
    return index


def main() -> int:
    teile = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    teile.add_argument("--ausgabe", required=True, type=Path, help="Ordner für index.json und die Bilder")
    args = teile.parse_args()
    try:
        lauf_job(args.ausgabe)
    except (JobFehler, urllib.error.HTTPError) as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
