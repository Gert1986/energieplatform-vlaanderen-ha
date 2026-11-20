"""
test_prijs.py
~~~~~~~~~~~~~

`custom_components/sef/prijs.py`: de opbouw van de prijs van nu als sensorattribuut.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "sef"))
from prijs import getal, opbouw_attribuut  # noqa: E402

DATA = {"opbouw": {"energie": "0.064", "net": "0.05", "heffingen": "0.0494", "btw": "0.0102"},
        "opbouw_injectie": {"beurs": "0.05", "marge": "-0.015"}}


def test_getal_zet_tekst_om_en_laat_none_staan():
    assert getal("0.0494") == 0.0494 and getal(None) is None


def test_afname_krijgt_energie_net_heffingen_en_btw_als_getal():
    assert opbouw_attribuut(DATA, "afname") == {"opbouw": {"energie": 0.064, "net": 0.05, "heffingen": 0.0494, "btw": 0.0102}}


def test_injectie_krijgt_beurs_en_marge_en_niet_de_afnameposten():
    assert opbouw_attribuut(DATA, "injectie") == {"opbouw": {"beurs": 0.05, "marge": -0.015}}


def test_oudere_server_of_geen_spotprijs_geeft_geen_attribuut():
    assert opbouw_attribuut({}, "afname") == {}
    assert opbouw_attribuut({"opbouw_injectie": None}, "injectie") == {}
    assert opbouw_attribuut({"opbouw": {}}, "afname") == {}


def test_prijs_nu_volgt_het_kwartier_van_de_klok_en_niet_de_laatste_opvraging():
    from datetime import datetime, timezone

    from p1 import kwartier_nu
    from prijs import prijs_nu

    data = {"afname_nu": "0.30", "kwartieren": [
        {"tijdstip": "2026-11-20T10:00:00+00:00", "afname": "0.30", "injectie": None},
        {"tijdstip": "2026-11-20T10:15:00+00:00", "afname": "0.25", "injectie": None}]}
    # Opgevraagd om 10:00, nu is het 10:20: de prijs van 10:15, niet die van de opvraging.
    assert prijs_nu(data, "afname", datetime(2026, 11, 20, 10, 20, tzinfo=timezone.utc), kwartier_nu) == 0.25
    # Injectie None in het kwartier: terugval op injectie_nu (hier afwezig) = None.
    assert prijs_nu(data, "injectie", datetime(2026, 11, 20, 10, 20, tzinfo=timezone.utc), kwartier_nu) is None
    # Buiten de kwartieren: de waarde van de laatste opvraging.
    assert prijs_nu(data, "afname", datetime(2026, 12, 1, tzinfo=timezone.utc), kwartier_nu) == 0.30
