"""
test_planner.py
~~~~~~~~~~~~~~~

`custom_components/sef/planner.py` (sinds 0.20.0): laden van het net of zelfverbruik, voor het kwartier van nu.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest

pytest.importorskip("homeassistant")
from custom_components.sef.planner import beslis  # noqa: E402

BEGIN = datetime.fromisoformat("2026-11-20T00:00:00+01:00")


def _kwartieren(prijzen: list[str]) -> list[dict]:
    return [{"tijdstip": (BEGIN + i * timedelta(minutes=15)).isoformat(), "afname": p} for i, p in enumerate(prijzen)]


DAG_NACHT = _kwartieren(["0.20"] * 28 + ["0.30"] * 60 + ["0.20"] * 8)  # nacht tot 07 u en vanaf 22 u
STANDAARD = dict(soc_pct=D("50"), capaciteit_kwh=D("5"), laadvermogen_kw=D("2.5"), piekruimte_kw=D("3"))


def test_dag_nacht_laadt_in_de_eerste_nachtkwartieren():
    b = beslis(DAG_NACHT, BEGIN + timedelta(minutes=5), **STANDAARD)  # 2,5 kWh tekort = 4 kwartieren
    assert (b["actie"], b["reden"], b["vermogen_kw"]) == ("laden", "goedkoop kwartier", D("2.5"))
    # Om 06:00 is het tekort niet gedekt door de 4 resterende nachtkwartieren: zelfde advies, vroegste eerst.
    assert beslis(DAG_NACHT, BEGIN + timedelta(hours=6), **STANDAARD)["actie"] == "laden"
    assert beslis(DAG_NACHT, BEGIN + timedelta(hours=6), **(STANDAARD | {"soc_pct": D("95")}))["actie"] == "laden"


def test_dynamisch_wacht_op_het_goedkoopste_kwartier():
    prijzen = _kwartieren(["0.25", "0.30", "0.05", "0.40", "0.45", "0.50"])
    b = beslis(prijzen, BEGIN, **(STANDAARD | {"soc_pct": D("90")}))  # 0,5 kWh = 1 kwartier
    assert (b["actie"], b["reden"]) == ("zelfverbruik", "later goedkoper") and "00:30" in b["detail"]
    b = beslis(prijzen, BEGIN + timedelta(minutes=30), **(STANDAARD | {"soc_pct": D("90")}))
    assert b["actie"] == "laden"


def test_laadt_voor_de_piek_van_vanavond_ook_als_morgen_goedkoper_is():
    # 13:00: 0,20 tot 17 u, piek 0,45 van 17 tot 21 u, daarna 0,25, morgenmiddag 0,05.
    prijzen = _kwartieren(["0.20"] * 16 + ["0.45"] * 16 + ["0.25"] * 60 + ["0.05"] * 16)
    b = beslis(prijzen, BEGIN, **(STANDAARD | {"soc_pct": D("90")}))
    assert b["actie"] == "laden" and "0,450" in b["detail"]
    b = beslis(prijzen, BEGIN + timedelta(hours=4), **STANDAARD)  # in de piek zelf: niet laden
    assert b["actie"] == "zelfverbruik"


def test_negatieve_prijs_laadt_en_te_klein_verschil_niet():
    vol = STANDAARD | {"soc_pct": D("90")}  # 0,5 kWh: 1 kwartier
    assert beslis(_kwartieren(["-0.02", "0.10", "0.30"]), BEGIN, **vol)["actie"] == "laden"
    assert beslis(_kwartieren(["0.20", "0.10"]), BEGIN, **STANDAARD)["reden"] == "nu duurst"
    b = beslis(_kwartieren(["0.28", "0.30", "0.31"]), BEGIN, **vol)  # 0,28 ≥ 0,31 × 0,85
    assert (b["actie"], b["reden"]) == ("zelfverbruik", "verschil te klein")


def test_vol_eigen_grens_en_zon():
    assert beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"soc_pct": D("100")}))["reden"] == "vol genoeg"
    assert beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"soc_pct": D("90")}), max_soc_pct=D("90"))["reden"] == "vol genoeg"
    assert beslis(DAG_NACHT, BEGIN, **STANDAARD, zon_kwh=D("3"))["reden"] == "vol genoeg"  # gisteren 3 kWh geïnjecteerd


def test_onbekende_laadtoestand_of_prijs():
    assert beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"soc_pct": None}))["reden"] == "laadtoestand onbekend"
    assert beslis(DAG_NACHT, BEGIN + timedelta(days=2), **STANDAARD)["reden"] == "geen prijs"
    assert beslis([], BEGIN, **STANDAARD)["reden"] == "geen prijs"


def test_piekruimte_begrenst_het_laadvermogen():
    assert beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"piekruimte_kw": D("1.2")}))["vermogen_kw"] == D("1.2")
    b = beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"piekruimte_kw": D("-0.5")}))
    assert (b["actie"], b["reden"]) == ("zelfverbruik", "geen piekruimte")
    assert beslis(DAG_NACHT, BEGIN, **(STANDAARD | {"piekruimte_kw": None}))["reden"] == "piekruimte onbekend"
