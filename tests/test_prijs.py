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
