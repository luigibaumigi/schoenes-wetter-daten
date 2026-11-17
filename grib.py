"""Kleiner GRIB2-Leser für die Dateien des DWD-Modells ICON-D2(-RUC).

Er kann genau das, was der neue Datenweg des DWD (opendata.dwd.de/weather/nwp/v1) liefert:
- eine Nachricht je Datei, Gitter-Vorlage 101 (das native Dreiecksgitter von ICON),
- Packung nach Vorlage 5.42 (CCSDS, entpackt mit libaec aus `imagecodecs`) oder 5.0 (einfach),
- eine Bitmap für fehlende Werte.

Alles andere lehnt er mit einer klaren Meldung ab, damit ein Formatwechsel beim DWD auffällt,
statt still falsche Bilder zu erzeugen.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np


class GribFehler(Exception):
    """Die Datei hat nicht den Aufbau, den der Daten-Job erwartet."""


@dataclass
class Feld:
    werte: np.ndarray  # float32, eine Zahl je Gitterzelle, NaN wo die Bitmap nichts liefert
    gitter_uuid: str  # Kennung des ICON-Gitters, muss zu den Koordinaten passen
    kategorie: int
    nummer: int
    vorlage_produkt: int
    referenzzeit: str  # Startzeit des Modelllaufs, "JJJJ-MM-TTTHH:MM" in UTC


def _abschnitte(daten: bytes):
    if daten[:4] != b"GRIB":
        raise GribFehler("keine GRIB-Datei")
    if daten[7] != 2:
        raise GribFehler(f"GRIB-Ausgabe {daten[7]}, erwartet 2")
    gesamt = struct.unpack(">Q", daten[8:16])[0]
    if gesamt != len(daten):
        raise GribFehler(f"Datei enthält {len(daten)} Bytes, die Nachricht nennt {gesamt} (mehrere Nachrichten oder abgeschnitten)")
    pos = 16
    while pos < gesamt - 4:
        laenge = struct.unpack(">I", daten[pos:pos + 4])[0]
        nummer = daten[pos + 4]
        yield nummer, daten[pos:pos + laenge]
        pos += laenge
    if daten[gesamt - 4:gesamt] != b"7777":
        raise GribFehler("Endkennung 7777 fehlt")


def _vorzeichen16(b: bytes) -> int:
    """GRIB speichert ganze Zahlen mit Vorzeichen-Bit statt Zweierkomplement."""
    roh = struct.unpack(">H", b)[0]
    return -(roh & 0x7FFF) if roh & 0x8000 else roh


def lies(daten: bytes) -> Feld:
    abschnitt: dict[int, bytes] = {}
    for nummer, inhalt in _abschnitte(daten):
        if nummer in abschnitt:
            raise GribFehler(f"Abschnitt {nummer} doppelt")
        abschnitt[nummer] = inhalt
    for noetig in (1, 3, 4, 5, 7):
        if noetig not in abschnitt:
            raise GribFehler(f"Abschnitt {noetig} fehlt")

    s1 = abschnitt[1]
    jahr = struct.unpack(">H", s1[12:14])[0]
    referenzzeit = f"{jahr:04d}-{s1[14]:02d}-{s1[15]:02d}T{s1[16]:02d}:{s1[17]:02d}"

    s3 = abschnitt[3]
    punkte = struct.unpack(">I", s3[6:10])[0]
    gitter_vorlage = struct.unpack(">H", s3[12:14])[0]
    if gitter_vorlage != 101:
        raise GribFehler(f"Gitter-Vorlage {gitter_vorlage}, erwartet 101 (ICON-Dreiecksgitter)")
    gitter_uuid = s3[19:35].hex()

    s4 = abschnitt[4]
    vorlage_produkt = struct.unpack(">H", s4[7:9])[0]
    kategorie, nummer = s4[9], s4[10]

    s5 = abschnitt[5]
    werte_anzahl = struct.unpack(">I", s5[5:9])[0]
    packung = struct.unpack(">H", s5[9:11])[0]
    r = struct.unpack(">f", s5[11:15])[0]
    e = _vorzeichen16(s5[15:17])
    d = _vorzeichen16(s5[17:19])
    bits = s5[19]

    s7 = abschnitt[7][5:]
    if bits == 0:
        ganz = np.zeros(werte_anzahl, dtype=np.float64)
    elif packung == 42:
        import imagecodecs

        flags, blockgroesse = s5[21], s5[22]
        rsi = struct.unpack(">H", s5[23:25])[0]
        bytes_je_wert = (bits + 7) // 8
        roh = imagecodecs.aec_decode(s7, bitspersample=bits, flags=flags, blocksize=blockgroesse, rsi=rsi)
        if len(roh) < werte_anzahl * bytes_je_wert:
            raise GribFehler(f"CCSDS lieferte {len(roh)} Bytes, erwartet {werte_anzahl * bytes_je_wert}")
        typ = {1: ">u1", 2: ">u2", 3: None, 4: ">u4"}[bytes_je_wert]
        if typ is None:
            b = np.frombuffer(roh, dtype=np.uint8)[: werte_anzahl * 3].reshape(-1, 3).astype(np.uint32)
            ganz = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]
        else:
            ganz = np.frombuffer(roh, dtype=typ)[:werte_anzahl]
        ganz = ganz.astype(np.float64)
    elif packung == 0:
        ganz = _einfach_entpacken(s7, bits, werte_anzahl)
    else:
        raise GribFehler(f"Packung 5.{packung} wird nicht unterstützt")

    werte = (r + ganz * (2.0 ** e)) / (10.0 ** d)

    s6 = abschnitt.get(6)
    if s6 is not None and s6[5] == 0:
        maske = np.unpackbits(np.frombuffer(s6[6:], dtype=np.uint8))[:punkte].astype(bool)
        if maske.sum() != werte_anzahl:
            raise GribFehler(f"Bitmap nennt {maske.sum()} Werte, Datenabschnitt {werte_anzahl}")
        voll = np.full(punkte, np.nan, dtype=np.float32)
        voll[maske] = werte
    elif s6 is None or s6[5] == 255:
        if werte_anzahl != punkte:
            raise GribFehler(f"{werte_anzahl} Werte für {punkte} Gitterpunkte ohne Bitmap")
        voll = werte.astype(np.float32)
    else:
        raise GribFehler(f"Bitmap-Kennung {s6[5]} wird nicht unterstützt")

    return Feld(voll, gitter_uuid, kategorie, nummer, vorlage_produkt, referenzzeit)


def _einfach_entpacken(daten: bytes, bits: int, anzahl: int) -> np.ndarray:
    if bits in (8, 16, 32):
        return np.frombuffer(daten, dtype={8: ">u1", 16: ">u2", 32: ">u4"}[bits])[:anzahl].astype(np.float64)
    alle = np.unpackbits(np.frombuffer(daten, dtype=np.uint8))[: anzahl * bits].reshape(anzahl, bits)
    gewichte = (1 << np.arange(bits - 1, -1, -1)).astype(np.uint64)
    return (alle.astype(np.uint64) @ gewichte).astype(np.float64)


def lies_datei(pfad) -> Feld:
    with open(pfad, "rb") as f:
        return lies(f.read())
