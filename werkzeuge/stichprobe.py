"""Stichprobe: veröffentlichte Wolken und Wind gegen die Darstellung des DWD-Kartendienstes (maps.dwd.de).

Wind:   eigener Wert an zehn Orten gegen das globale DWD-Modell ICON (0,25°, Ebene `Icon_reg025_fd_sl_UV10M`).
        Andere Auflösung, eigener Lauf: Abweichungen von einigen km/h und einigen zehn Grad bei schwachem Wind
        sind normal. Auffällig wären vertauschte Richtungen (180°) oder ein Faktor in der Stärke.
Wolken: eigenes Bild (Bedeckung und Dicke) neben dem Satellitenbild des DWD zur selben Stunde
        (Ebene `Satellite_meteosat_1km_euat_rgb_day_hrv_and_night_ir108_3h`, alle 3 Stunden), gleicher Ausschnitt
        in Web-Mercator. Ergebnis als Bild zum Ansehen.

Aufruf:  python werkzeuge/stichprobe.py [--ausgabe stichprobe.png]
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SEITE = "https://luigibaumigi.github.io/schoenes-wetter-daten/"
DWD_WMS = "https://maps.dwd.de/geoserver/dwd/wms"
KENNUNG = {"User-Agent": "schoenes-wetter-stichprobe/1"}
ORTE = [
    ("List auf Sylt", 55.02, 8.43), ("Hamburg", 53.55, 9.99), ("Rostock", 54.09, 12.10), ("Berlin", 52.52, 13.40),
    ("Köln", 50.94, 6.96), ("Leipzig", 51.34, 12.37), ("Frankfurt", 50.11, 8.68), ("Nürnberg", 49.45, 11.08),
    ("Freiburg", 47.99, 7.85), ("München", 48.14, 11.58),
]
R = 6378137.0


def hole(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=KENNUNG), timeout=90) as antwort:
        return antwort.read()


def bild_holen(url: str) -> Image.Image:
    daten = hole(url)
    if daten[1:4] != b"PNG":
        raise RuntimeError(f"Kein Bild vom DWD: {daten[:400]!r}")
    return Image.open(io.BytesIO(daten))


def merc_y(breite: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(breite) / 2))


def bildpunkt(gitter: dict, breite: float, laenge: float) -> tuple[float, float]:
    """Spalte und Zeile (Gleitkomma, Mitte des ersten Bildpunkts = 0) wie Radarraster in der App."""
    sp = (laenge - gitter["west"]) / (gitter["ost"] - gitter["west"]) * gitter["spalten"] - 0.5
    yn, ys = merc_y(gitter["nord"]), merc_y(gitter["sued"])
    ze = (yn - merc_y(breite)) / (yn - ys) * gitter["zeilen"] - 0.5
    return sp, ze


def bilinear(feld: np.ndarray, sp: float, ze: float) -> float:
    x0, y0 = int(math.floor(sp)), int(math.floor(ze))
    fx, fy = sp - x0, ze - y0
    a = feld[y0:y0 + 2, x0:x0 + 2].astype(float)
    return float(a[0, 0] * (1 - fx) * (1 - fy) + a[0, 1] * fx * (1 - fy) + a[1, 0] * (1 - fx) * fy + a[1, 1] * fx * fy)


def richtung_woher(u: float, v: float) -> float:
    return (math.degrees(math.atan2(-u, -v)) + 360) % 360


def dwd_wind(breite: float, laenge: float, zeit: str) -> tuple[float, float, str]:
    x, y = math.radians(laenge) * R, merc_y(breite) * R
    d = 2000
    q = {
        "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "styles": "",
        "layers": "Icon_reg025_fd_sl_UV10M", "query_layers": "Icon_reg025_fd_sl_UV10M",
        "crs": "EPSG:3857", "bbox": f"{x - d},{y - d},{x + d},{y + d}", "width": 3, "height": 3, "i": 1, "j": 1,
        "info_format": "application/json", "time": zeit,
    }
    p = json.loads(hole(DWD_WMS + "?" + urllib.parse.urlencode(q)))["features"][0]["properties"]
    return p["u"], p["v"], p["REFERENCE_TIME"]


def wolken_ansicht(bild: np.ndarray) -> Image.Image:
    """Einfache Darstellung zum Vergleichen: dunkler Grund, Wolken weiß nach Bedeckung × Dicke."""
    bedeckung, dicke, gueltig = bild[..., 0] / 255, bild[..., 1] / 255, bild[..., 2] > 0
    a = np.clip(bedeckung * (0.3 + 0.7 * np.clip(dicke / 0.5, 0, 1)), 0, 1)
    grau = 30 + a * 225
    grau[~gueltig] = 0
    return Image.fromarray(np.stack([grau] * 3, -1).astype(np.uint8))


def main() -> None:
    teile = argparse.ArgumentParser()
    teile.add_argument("--ausgabe", type=Path, default=Path("stichprobe.png"))
    args = teile.parse_args()

    index = json.loads(hole(SEITE + "index.json"))
    print(f"Veröffentlicht: erzeugt {index['erzeugt']}, {index['modell']}, jüngster Lauf {index['neuesterLauf']}, "
          f"{len(index['zeiten'])} Stunden")
    jetzt = dt.datetime.now(dt.timezone.utc)
    zeiten = {e["zeit"]: e for e in index["zeiten"]}

    # Wind: jüngste volle Stunde, die schon vorbei ist
    stunde = jetzt.replace(minute=0, second=0, microsecond=0)
    e = zeiten.get(stunde.strftime("%Y-%m-%dT%H:%M:%SZ")) or index["zeiten"][0]
    wind = np.asarray(Image.open(io.BytesIO(hole(SEITE + e["wind"]))).convert("RGB"))
    g = index["wind"]["gitter"]
    u_feld = (wind[..., 0].astype(float) - 128) / 3
    v_feld = (wind[..., 1].astype(float) - 128) / 3
    print(f"\nWind 10 m, {e['zeit']} (eigener Lauf {e['lauf']}) gegen DWD ICON 0,25°")
    print(f"{'Ort':14} {'eigen km/h':>10} {'aus °':>6}   {'DWD km/h':>8} {'aus °':>6}   Differenz Richtung")
    for name, br, la in ORTE:
        sp, ze = bildpunkt(g, br, la)
        u, v = bilinear(u_feld, sp, ze), bilinear(v_feld, sp, ze)
        du, dv, lauf = dwd_wind(br, la, e["zeit"].replace("Z", ".000Z"))
        s, ds = math.hypot(u, v) * 3.6, math.hypot(du, dv) * 3.6
        r, dr = richtung_woher(u, v), richtung_woher(du, dv)
        diff = (r - dr + 180) % 360 - 180
        print(f"{name:14} {s:10.0f} {r:6.0f}   {ds:8.0f} {dr:6.0f}   {diff:+5.0f}°")

    # Wolken: jüngste Satellitenstunde (alle 3 h, kommt mit Verzögerung), die es auch bei uns gibt
    gw = index["wolken"]["gitter"]
    x0, x1 = math.radians(gw["west"]) * R, math.radians(gw["ost"]) * R
    y0, y1 = merc_y(gw["sued"]) * R, merc_y(gw["nord"]) * R
    sat = e = q = None
    kandidat = stunde - dt.timedelta(hours=stunde.hour % 3)
    for _ in range(4):
        e = zeiten.get(kandidat.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if e:
            q = {
                "service": "WMS", "version": "1.3.0", "request": "GetMap", "styles": "", "format": "image/png",
                "layers": "Satellite_meteosat_1km_euat_rgb_day_hrv_and_night_ir108_3h", "crs": "EPSG:3857",
                "bbox": f"{x0},{y0},{x1},{y1}", "width": gw["spalten"], "height": gw["zeilen"],
                "time": e["zeit"].replace("Z", ".000Z"),
            }
            try:
                sat = bild_holen(DWD_WMS + "?" + urllib.parse.urlencode(q)).convert("RGB")
                break
            except RuntimeError:
                pass
        kandidat -= dt.timedelta(hours=3)
    if sat is None:
        print("Wolken: kein passendes Satellitenbild des DWD gefunden")
        return
    eigen = wolken_ansicht(np.asarray(Image.open(io.BytesIO(hole(SEITE + e["wolken"]))).convert("RGB")))
    ohne_zeit = {k: v for k, v in q.items() if k != "time"}
    try:
        grenzen = Image.open(io.BytesIO(hole(DWD_WMS + "?" + urllib.parse.urlencode(
            {**ohne_zeit, "layers": "dwd:Warngebiete_Bundeslaender", "transparent": "true"})))).convert("RGBA")
    except Exception as fehler:  # noqa: BLE001 - Grenzen sind nur Orientierung
        print(f"(Grenzen nicht geladen: {fehler})")
        grenzen = Image.new("RGBA", sat.size, (0, 0, 0, 0))
    breite, hoehe = eigen.size
    gesamt = Image.new("RGB", (breite * 2 + 10, hoehe + 24), (255, 255, 255))
    for i, (bild, titel) in enumerate([(eigen, f"Daten-Job {e['zeit']} (Lauf {e['lauf']})"),
                                       (sat, f"DWD Satellit {e['zeit']}")]):
        bild = bild.convert("RGBA")
        bild.alpha_composite(grenzen)
        gesamt.paste(bild.convert("RGB"), (i * (breite + 10), 24))
        ImageDraw.Draw(gesamt).text((i * (breite + 10) + 4, 6), titel, fill=(0, 0, 0))
    gesamt.save(args.ausgabe)
    print(f"\nWolken: {args.ausgabe} (links Daten-Job, rechts DWD-Satellit, Grenzen der Bundesländer vom DWD)")


if __name__ == "__main__":
    main()
