"""Zielgitter wie `Radarraster` der App und die Umrechnung vom Dreiecksgitter."""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gitter  # noqa: E402


class GitterTest(unittest.TestCase):
    def test_bildpunkte_sind_auf_der_karte_quadratisch(self):
        g = gitter.quadratisch(1.0, 45.0, 19.0, 57.0, 540)
        self.assertEqual(g.zeilen, 576)
        breite = math.radians(18.0) / g.spalten
        hoehe = (float(gitter.mercator(57.0)) - float(gitter.mercator(45.0))) / g.zeilen
        self.assertAlmostEqual(breite / hoehe, 1.0, delta=0.002)

    def test_zeile_null_ist_norden_und_zeilen_sind_in_mercator_gleich_hoch(self):
        g = gitter.Zielgitter(0.0, 40.0, 10.0, 60.0, 10, 20)
        laenge, breite = g.mittelpunkte()
        self.assertGreater(breite[0, 0], breite[-1, 0])
        self.assertAlmostEqual(laenge[0, 0], 0.5)
        self.assertAlmostEqual(laenge[0, -1], 9.5)
        y = gitter.mercator(breite[:, 0])
        abstaende = np.diff(y)
        np.testing.assert_allclose(abstaende, abstaende[0], rtol=1e-9)
        # wie Radarraster.zeileVon in der App: y = (mercNord - merc(breite)) / (mercNord - mercSued) * zeilen
        yn, ys = gitter.mercator(60.0), gitter.mercator(40.0)
        zeile = (yn - gitter.mercator(breite[7, 0])) / (yn - ys) * g.zeilen
        self.assertAlmostEqual(float(zeile), 7.5, places=6)

    def test_umrechnung_folgt_einem_glatten_feld_und_laesst_luecken_leer(self):
        # künstliches „Modell“: Punkte alle 0,02° zwischen 5 und 10° Ost, 48 und 52° Nord, Wert = Länge
        la, br = np.meshgrid(np.arange(5.0, 10.0, 0.02), np.arange(48.0, 52.0, 0.02))
        werte = la.ravel().astype(np.float32)
        werte[::7] = np.nan  # einzelne fehlende Zellen werden von den Nachbarn überbrückt
        g = gitter.quadratisch(4.0, 47.0, 11.0, 53.0, 70)
        u = gitter.umrechnung(g, la.ravel(), br.ravel())
        ergebnis = u.anwenden(werte)
        laenge, breite = g.mittelpunkte()
        innen = (laenge > 5.1) & (laenge < 9.9) & (breite > 48.1) & (breite < 51.9)
        np.testing.assert_allclose(ergebnis[innen], laenge[innen], atol=0.02)
        aussen = (laenge < 4.9) | (breite > 52.1)
        self.assertTrue(np.isnan(ergebnis[aussen]).all())


if __name__ == "__main__":
    unittest.main()
