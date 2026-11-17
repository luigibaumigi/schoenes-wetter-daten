"""GRIB-Leser mit einer echten Datei des DWD (ICON-D2-RUC, Lauf 17.11.2026 08 UTC, +1 h, Gesamtbewölkung)."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grib  # noqa: E402

DATEI = Path(__file__).parent / "daten" / "icon-d2-ruc_CLCT_2026-11-17T0800_PT001H00M.grib2"


class GribTest(unittest.TestCase):
    def setUp(self):
        self.daten = DATEI.read_bytes()

    def test_liest_die_gesamtbewoelkung(self):
        f = grib.lies(self.daten)
        self.assertEqual(f.referenzzeit, "2026-11-17T08:00")
        self.assertEqual((f.kategorie, f.nummer), (6, 1))  # Bewölkung, Gesamtbedeckung
        self.assertEqual(f.werte.shape, (542040,))  # Zellen des ICON-D2-Gitters
        self.assertTrue(f.gitter_uuid.startswith("c6b12daa"))
        self.assertEqual(int(np.isnan(f.werte).sum()), 16968)  # Randzellen ohne Wert (Bitmap)
        self.assertAlmostEqual(float(np.nanmin(f.werte)), 0.0, places=3)
        self.assertAlmostEqual(float(np.nanmax(f.werte)), 100.0, places=3)
        self.assertAlmostEqual(float(np.nanmean(f.werte)), 92.6, delta=0.1)  # 17.11.: fast überall bedeckt

    def test_stimmt_mit_eccodes_ueberein(self):
        """Gegenprobe mit der Referenz-Bibliothek des EZMW; läuft nur, wo sie installiert ist (Linux)."""
        try:
            import eccodes
        except Exception:  # noqa: BLE001 - jede Art von „nicht da“
            self.skipTest("eccodes nicht installiert")
        handle = eccodes.codes_new_from_message(self.daten)
        try:
            referenz = eccodes.codes_get_double_array(handle, "values")
            fehlwert = eccodes.codes_get_double(handle, "missingValue")
        finally:
            eccodes.codes_release(handle)
        eigen = grib.lies(self.daten).werte
        referenz = np.where(referenz == fehlwert, np.nan, referenz)
        np.testing.assert_array_equal(np.isnan(eigen), np.isnan(referenz))
        np.testing.assert_allclose(eigen[~np.isnan(eigen)], referenz[~np.isnan(referenz)], atol=1e-3)

    def test_meldet_abgeschnittene_datei(self):
        with self.assertRaises(grib.GribFehler):
            grib.lies(self.daten[:-1000])

    def test_meldet_anderes_gitter(self):
        kaputt = bytearray(self.daten)
        # Abschnitte nach dem Kopf (16 Bytes) durchgehen bis Abschnitt 3; Gitter-Vorlage in Byte 13–14 davon
        s3 = 16
        while kaputt[s3 + 4] != 3:
            s3 += int.from_bytes(kaputt[s3:s3 + 4], "big")
        kaputt[s3 + 12:s3 + 14] = (0).to_bytes(2, "big")  # Vorlage 0 = reguläres Gitter
        with self.assertRaisesRegex(grib.GribFehler, "Gitter-Vorlage 0"):
            grib.lies(bytes(kaputt))

    def test_meldet_keine_grib_datei(self):
        with self.assertRaises(grib.GribFehler):
            grib.lies(b"<html>404</html>")


if __name__ == "__main__":
    unittest.main()
