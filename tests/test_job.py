"""Planung der Stunden und Kodierung der Bilder. Ohne Netz: die Verzeichnisseiten des DWD kommen aus
aufgezeichneten Antworten (tests/daten/liste_*.html) oder sind im selben Aufbau nachgebaut."""

import datetime as dt
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daten_job as job  # noqa: E402

DATEN = Path(__file__).parent / "daten"
UTC = dt.timezone.utc


def seite(eintraege):
    """Verzeichnisseite im Aufbau von opendata.dwd.de (nginx autoindex)."""
    zeilen = ['<a href="../">../</a>'] + [f'<a href="{e}">{e}</a>   17-Nov-2026 08:40:23   470233' for e in eintraege]
    return ("<html><body><pre>" + "\n".join(zeilen) + "</pre></body></html>").encode()


class DwdAttrappe:
    """Antwortet wie opendata.dwd.de für ein Modell mit den angegebenen Läufen und Schritten."""

    def __init__(self, laeufe_je_modell, schritte, fehlend=()):
        self.laeufe_je_modell = laeufe_je_modell  # {"icon-d2-ruc": [datetime, …]}
        self.schritte = schritte  # Anzahl Stunden je Lauf: 0..schritte-1
        self.fehlend = set(fehlend)  # {(modell, variable, lauf, stunde)}

    def __call__(self, url, versuche=4):
        rest = url[len(job.BASIS) + 1:]
        teile = rest.split("/")
        modell, variable = teile[0], teile[2]
        laeufe = self.laeufe_je_modell.get(modell, [])
        if rest.endswith("/r/"):
            return seite([f"{l:%Y-%m-%dT%H}%3A00/" for l in laeufe])
        if rest.endswith("/s/"):
            lauf = dt.datetime.strptime(job.urllib.parse.unquote(teile[4]), "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
            if lauf not in laeufe:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            stunden = [h for h in range(self.schritte) if (modell, variable, lauf, h) not in self.fehlend]
            return seite([job.schritt_datei(h) for h in stunden])
        raise AssertionError(f"unerwarteter Abruf {url}")


def stunden(von, bis):
    return [von + dt.timedelta(hours=h) for h in range(int((bis - von).total_seconds() // 3600) + 1)]


class ListeTest(unittest.TestCase):
    def test_liest_die_aufgezeichnete_liste_der_laeufe(self):
        with mock.patch.object(job, "hole", return_value=(DATEN / "liste_laeufe_CLCT.html").read_bytes()):
            laeufe = job.laeufe(job.MODELLE[0], "CLCT")
        self.assertEqual(len(laeufe), 24)
        self.assertEqual(laeufe[0], dt.datetime(2026, 11, 16, 9, tzinfo=UTC))
        self.assertEqual(laeufe[-1], dt.datetime(2026, 11, 17, 8, tzinfo=UTC))

    def test_liest_die_aufgezeichnete_liste_der_schritte(self):
        with mock.patch.object(job, "hole", return_value=(DATEN / "liste_schritte_CLCT_0800.html").read_bytes()):
            schritte = job.vorhandene_schritte(job.MODELLE[0], "CLCT", dt.datetime(2026, 11, 17, 8, tzinfo=UTC))
        # aufgezeichnet um 09:03 UTC: Ein RUC-Lauf rechnet 27 Stunden voraus, die Stunden kommen nach und nach.
        # Bis +8 h (was der Job braucht) liegt alles rund 47 Minuten nach dem Start bereit.
        self.assertEqual(schritte, set(range(0, 17)))


class PlanungTest(unittest.TestCase):
    jetzt = dt.datetime(2026, 11, 17, 9, 55, tzinfo=UTC)

    def test_vergangenheit_aus_vorlauf_null_zukunft_aus_dem_juengsten_lauf(self):
        dwd = DwdAttrappe({"icon-d2-ruc": stunden(dt.datetime(2026, 11, 16, 10, tzinfo=UTC), dt.datetime(2026, 11, 17, 9, tzinfo=UTC))}, 9)
        with mock.patch.object(job, "hole", dwd):
            modell, neuester, bilder = job.plane(self.jetzt)
        self.assertEqual(modell.name, "ICON-D2-RUC")
        self.assertEqual(neuester, dt.datetime(2026, 11, 17, 9, tzinfo=UTC))
        self.assertEqual(bilder[0].zeit, dt.datetime(2026, 11, 17, 6, tzinfo=UTC))  # 3 Stunden zurück
        self.assertEqual(bilder[-1].zeit, dt.datetime(2026, 11, 17, 17, tzinfo=UTC))  # +8 h
        self.assertEqual([b.vorlauf for b in bilder], [0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(len({b.zeit for b in bilder}), len(bilder))

    def test_unvollstaendiger_neuester_lauf_wird_uebersprungen(self):
        laeufe = stunden(dt.datetime(2026, 11, 17, 0, tzinfo=UTC), dt.datetime(2026, 11, 17, 9, tzinfo=UTC))
        neun = dt.datetime(2026, 11, 17, 9, tzinfo=UTC)
        dwd = DwdAttrappe({"icon-d2-ruc": laeufe}, 9, fehlend={("icon-d2-ruc", "TQI", neun, 7)})
        with mock.patch.object(job, "hole", dwd):
            _, neuester, bilder = job.plane(self.jetzt)
        self.assertEqual(neuester, dt.datetime(2026, 11, 17, 8, tzinfo=UTC))
        self.assertEqual(bilder[-1].zeit, dt.datetime(2026, 11, 17, 16, tzinfo=UTC))
        # 09 UTC kommt dann aus dem Lauf von 08 UTC (+1 h), nicht aus dem unvollständigen
        neun_uhr = next(b for b in bilder if b.zeit == neun)
        self.assertEqual(neun_uhr.lauf, dt.datetime(2026, 11, 17, 8, tzinfo=UTC))

    def test_ersatzmodell_wenn_ruc_veraltet(self):
        alt = stunden(dt.datetime(2026, 11, 16, 20, tzinfo=UTC), dt.datetime(2026, 11, 17, 2, tzinfo=UTC))
        d2 = [dt.datetime(2026, 11, 17, h, tzinfo=UTC) for h in (0, 3, 6)]
        dwd = DwdAttrappe({"icon-d2-ruc": alt, "icon-d2": d2}, 13)
        with mock.patch.object(job, "hole", dwd):
            modell, neuester, bilder = job.plane(self.jetzt)
        self.assertEqual(modell.name, "ICON-D2")
        self.assertEqual(neuester, dt.datetime(2026, 11, 17, 6, tzinfo=UTC))
        sieben = next(b for b in bilder if b.zeit == dt.datetime(2026, 11, 17, 7, tzinfo=UTC))
        self.assertEqual(sieben.lauf, dt.datetime(2026, 11, 17, 6, tzinfo=UTC))
        sechs_vorher = next(b for b in bilder if b.zeit == dt.datetime(2026, 11, 17, 6, tzinfo=UTC))
        self.assertEqual(sechs_vorher.vorlauf, 0)

    def test_fehler_wenn_nichts_brauchbares_da_ist(self):
        alt = stunden(dt.datetime(2026, 11, 16, 20, tzinfo=UTC), dt.datetime(2026, 11, 17, 2, tzinfo=UTC))
        dwd = DwdAttrappe({"icon-d2-ruc": alt}, 9)
        with mock.patch.object(job, "hole", dwd):
            with self.assertRaisesRegex(job.JobFehler, "zu alt"):
                job.plane(self.jetzt)


class KodierungTest(unittest.TestCase):
    def test_wolkenbild(self):
        clct = np.array([[0.0, 50.0, 100.0, np.nan]], dtype=np.float32)
        tqc = np.array([[0.0, 0.1, 1.0, np.nan]], dtype=np.float32)
        tqi = np.zeros_like(tqc)
        b = job.wolkenbild(clct, tqc, tqi)
        self.assertEqual(b.dtype, np.uint8)
        self.assertEqual(b[0, :, 0].tolist(), [0, 130, 255, 0])  # 50 % → Stufe 32 von 63 → 130
        self.assertEqual(b[0, :, 2].tolist(), [255, 255, 255, 0])
        self.assertEqual(b[0, 0, 1], 0)
        self.assertEqual(b[0, 3, 1], 0)
        # Wolkendicke: 0,1 kg/m² Wasser → τ = 15 → 15 / 28 ≈ 0,54; 1 kg/m² → τ = 150 → 0,92
        self.assertAlmostEqual(b[0, 1, 1] / 255, 15 / 28, delta=1 / 63)
        self.assertAlmostEqual(b[0, 2, 1] / 255, 150 / 163, delta=1 / 63)
        # jeder Wert liegt genau auf einer der 64 Stufen
        stufen = {round(i * 255 / 63) for i in range(64)}
        self.assertTrue(set(b[..., :2].ravel().tolist()) <= stufen)

    def test_windbild(self):
        u = np.array([[0.0, 10.0, -5.0, 60.0, np.nan]], dtype=np.float32)
        v = np.array([[0.0, -2.0, 4.0, -60.0, 1.0]], dtype=np.float32)
        b = job.windbild(u, v)
        self.assertEqual(b[0, :, 0].tolist(), [128, 158, 113, 255, 128])
        self.assertEqual(b[0, :, 1].tolist(), [128, 122, 140, 1, 128])
        self.assertEqual(b[0, :, 2].tolist(), [255, 255, 255, 255, 0])
        # Rückweg wie in der App: (Wert - 128) / 3
        self.assertAlmostEqual((b[0, 1, 0] - 128) / 3, 10.0)


if __name__ == "__main__":
    unittest.main()
