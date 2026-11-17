"""Umrechnung vom ICON-Dreiecksgitter auf das Kartengitter der App.

Das Zielgitter folgt derselben Regel wie `Radarraster` in der App (shared/…/modell/Radar.kt):
Spalten gleich breit in geografischer Länge, Zeilen gleich hoch in Web-Mercator, Zeile 0 oben (Norden).
So passt ein Bild ohne Verzerrung als MapLibre-ImageSource auf die Karte, mit den vier Ecken des Ausschnitts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

ERDRADIUS_KM = 6371.0


def mercator(breite: float | np.ndarray):
    return np.log(np.tan(np.pi / 4 + np.radians(breite) / 2))


def breite_aus_mercator(y: np.ndarray) -> np.ndarray:
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


@dataclass(frozen=True)
class Zielgitter:
    west: float
    sued: float
    ost: float
    nord: float
    spalten: int
    zeilen: int

    def mittelpunkte(self) -> tuple[np.ndarray, np.ndarray]:
        """Länge und Breite der Bildpunkt-Mitten, je als Feld [zeilen, spalten]."""
        laenge = self.west + (np.arange(self.spalten) + 0.5) * (self.ost - self.west) / self.spalten
        yn, ys = mercator(self.nord), mercator(self.sued)
        y = yn - (np.arange(self.zeilen) + 0.5) * (yn - ys) / self.zeilen
        breite = breite_aus_mercator(y)
        return np.meshgrid(laenge, breite)

    def als_json(self) -> dict:
        return {
            "west": self.west,
            "sued": self.sued,
            "ost": self.ost,
            "nord": self.nord,
            "spalten": self.spalten,
            "zeilen": self.zeilen,
        }


def quadratisch(west: float, sued: float, ost: float, nord: float, spalten: int) -> Zielgitter:
    """Zeilenzahl so, dass die Bildpunkte auf der Karte (Web-Mercator) quadratisch sind."""
    breite_x = math.radians(ost - west)
    hoehe_y = float(mercator(nord) - mercator(sued))
    return Zielgitter(west, sued, ost, nord, spalten, round(spalten * hoehe_y / breite_x))


def _einheitsvektoren(laenge: np.ndarray, breite: np.ndarray) -> np.ndarray:
    la, br = np.radians(laenge), np.radians(breite)
    return np.stack([np.cos(br) * np.cos(la), np.cos(br) * np.sin(la), np.sin(br)], axis=-1)


@dataclass
class Umrechnung:
    """Für jeden Bildpunkt die k nächsten ICON-Zellen und ihre Gewichte (umgekehrter Abstand)."""

    gitter: Zielgitter
    index: np.ndarray  # [zeilen*spalten, k]
    gewicht: np.ndarray  # [zeilen*spalten, k]
    ausserhalb: np.ndarray  # [zeilen*spalten] bool: kein Modellwert in der Nähe

    def anwenden(self, werte: np.ndarray) -> np.ndarray:
        """Werte des Dreiecksgitters auf das Zielgitter. NaN, wo das Modell nichts hat."""
        nachbarn = werte[self.index]
        gueltig = ~np.isnan(nachbarn)
        g = np.where(gueltig, self.gewicht, 0.0)
        summe = g.sum(axis=1)
        ergebnis = (np.where(gueltig, nachbarn, 0.0) * g).sum(axis=1) / np.where(summe > 0, summe, 1.0)
        ergebnis[(summe <= 0) | self.ausserhalb] = np.nan
        return ergebnis.reshape(self.gitter.zeilen, self.gitter.spalten).astype(np.float32)


def umrechnung(gitter: Zielgitter, zell_laenge: np.ndarray, zell_breite: np.ndarray, k: int = 4,
               max_abstand_km: float = 3.0) -> Umrechnung:
    baum = cKDTree(_einheitsvektoren(zell_laenge, zell_breite))
    laenge, breite = gitter.mittelpunkte()
    ziel = _einheitsvektoren(laenge.ravel(), breite.ravel())
    abstand, index = baum.query(ziel, k=k)
    abstand_km = abstand * ERDRADIUS_KM  # Sehne ≈ Bogen bei so kleinen Abständen
    gewicht = 1.0 / np.maximum(abstand_km, 0.05) ** 2
    ausserhalb = abstand_km[:, 0] > max_abstand_km
    return Umrechnung(gitter, index, gewicht, ausserhalb)
