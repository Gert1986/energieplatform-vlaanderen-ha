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


# Prijssignalen (sinds 0.18.0): rang en goedkoopste venster.
from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from prijs import goedkoopste_venster, horizon, rang  # noqa: E402

BRUSSEL = ZoneInfo("Europe/Brussels")


def _dag(datum: str, prijzen=None) -> list[dict]:
    """De kwartieren van een lokale dag zoals SEF ze stuurt (tijdstip met eigen offset); prijs = volgnummer
    tenzij `prijzen` (een functie van het volgnummer) iets anders zegt."""
    begin = datetime.fromisoformat(datum).replace(tzinfo=BRUSSEL).astimezone(timezone.utc)
    einde = (datetime.fromisoformat(datum) + timedelta(days=1)).replace(tzinfo=BRUSSEL).astimezone(timezone.utc)
    rij, t = [], begin
    while t < einde:
        i = len(rij)
        rij.append({"tijdstip": t.astimezone(BRUSSEL).isoformat(), "afname": str(prijzen(i) if prijzen else i)})
        t += timedelta(minutes=15)
    return rij


def test_horizon_telt_92_96_en_100_kwartieren_en_loopt_over_de_klokwissel():
    for datum, aantal in (("2026-11-20", 96), ("2026-03-29", 92), ("2026-10-25", 100)):
        dag = _dag(datum)
        assert len(dag) == aantal
        assert len(horizon(dag, datetime.fromisoformat(dag[0]["tijdstip"]))) == aantal


def test_rang_is_het_percentage_strikt_goedkoper_en_dag_nacht_geeft_nacht_nul():
    dag = _dag("2026-11-20")  # prijs stijgt: het eerste kwartier is het goedkoopste
    nu = datetime.fromisoformat(dag[0]["tijdstip"]) + timedelta(minutes=5)
    assert rang(dag, nu) == 0
    assert rang(dag, nu + timedelta(hours=12)) == 0  # vanaf 12 u is het kwartier van nu weer het goedkoopste
    daluur = _dag("2026-11-20", lambda i: "0.20" if i < 28 or i >= 88 else "0.30")  # nacht 22-07 u
    assert rang(daluur, datetime.fromisoformat(daluur[0]["tijdstip"])) == 0
    middag = datetime.fromisoformat(daluur[48]["tijdstip"])  # 12 u: dag; nog 40 dag- en 8 nachtkwartieren
    assert rang(daluur, middag) == 100 * 8 // 48 and len({p for _, p in horizon(daluur, middag)}) == 2


def test_negatieve_prijzen_rekenen_exact():
    dag = _dag("2026-06-14", lambda i: f"{(i - 50) / 1000:.3f}")  # -0.050 tot 0.045
    v = goedkoopste_venster(dag, datetime.fromisoformat(dag[0]["tijdstip"]), 4)
    assert v["begin"] == datetime.fromisoformat(dag[0]["tijdstip"]) and v["gemiddelde"] == Decimal("-0.0485")
    assert rang(dag, datetime.fromisoformat(dag[60]["tijdstip"])) == 0


def test_goedkoopste_venster_kiest_het_goedkoopste_blok_vanaf_het_kwartier_van_nu():
    prijzen = {40: "0.10", 41: "0.10", 42: "0.10", 43: "0.10", 44: "0.12"}  # 10:00-11:00 (gem. 0.10) wint van 10:15
    dag = _dag("2026-11-20", lambda i: prijzen.get(i, "0.30"))
    tien = datetime.fromisoformat(dag[40]["tijdstip"])
    v = goedkoopste_venster(dag, tien - timedelta(hours=3), 4)
    assert (v["begin"], v["einde"], v["gemiddelde"]) == (tien, tien + timedelta(hours=1), Decimal("0.10"))
    # Na het blok: nooit een blok dat al begonnen was (10:15 met drie goedkope kwartieren), maar ten vroegste nu.
    assert goedkoopste_venster(dag, tien + timedelta(hours=1, minutes=5), 4)["begin"] == tien + timedelta(hours=1)


def test_te_korte_horizon_of_gat_of_laatste_dag_van_de_maand():
    dag = _dag("2026-11-30")  # SEF stopt op de maandgrens: de horizon stopt om middernacht
    laat = datetime.fromisoformat(dag[-1]["tijdstip"])
    assert horizon(dag, laat)[-1][0] == laat and goedkoopste_venster(dag, laat, 4) is None
    assert goedkoopste_venster(dag, laat - timedelta(minutes=45), 4)["einde"] == laat + timedelta(minutes=15)  # middernacht
    assert rang(dag, laat) is None
    gat = dag[:10] + dag[11:]
    assert len(horizon(gat, datetime.fromisoformat(dag[0]["tijdstip"]))) == 10
    assert rang([], laat) is None and rang(dag, laat + timedelta(hours=1)) is None
