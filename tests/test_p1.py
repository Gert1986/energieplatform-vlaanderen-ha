"""
test_p1.py
~~~~~~~~~~

Van 5-minutenblokken naar kwartierrijen (`custom_components/sef/p1.py`).

:copyright: (c) 2026 Gert Botte
:license: Alle rechten voorbehouden
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "sef"))
from p1 import BLOK, KWARTIER, in_stukken, kwartiergrens, kwartierrijen  # noqa: E402

T0 = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


def _blokken(waarden: dict[str, list[float]], start=T0):
    """Per kanaal een lijst opeenvolgende 5-minutenwaarden vanaf `start`."""
    return {k: {start + i * BLOK: w for i, w in enumerate(v)} for k, v in waarden.items()}


def test_drie_blokken_per_kwartier_en_twee_richtingen():
    blokken = _blokken({"afname_1": [0.1, 0.1, 0.1, 0.2, 0.0, 0.0], "injectie_1": [0, 0, 0.05, 0, 0, 0]})
    rijen = kwartierrijen(blokken, vanaf=T0, tot=T0 + 2 * KWARTIER)
    assert rijen == [
        {"tijdstip": "2026-10-06T08:00:00+00:00", "register": "dag", "richting": "afname", "kwh": "0.300000"},
        {"tijdstip": "2026-10-06T08:00:00+00:00", "register": "dag", "richting": "injectie", "kwh": "0.050000"},
        {"tijdstip": "2026-10-06T08:15:00+00:00", "register": "dag", "richting": "afname", "kwh": "0.200000"},
        {"tijdstip": "2026-10-06T08:15:00+00:00", "register": "dag", "richting": "injectie", "kwh": "0.000000"},
    ]


def test_register_volgt_het_telwerk_dat_bewoog():
    # Tarief 2 (nacht) bewoog; daarna niets: het register blijft nacht. Een wissel binnen het
    # kwartier: alles op het grootste telwerk, één register (SEF weigert er twee).
    blokken = _blokken({"afname_1": [0, 0, 0, 0, 0, 0, 0.1, 0, 0], "afname_2": [0.2, 0.2, 0, 0, 0, 0, 0.05, 0.05, 0.05]})
    rijen = [r for r in kwartierrijen(blokken, vanaf=T0, tot=T0 + 3 * KWARTIER) if r["richting"] == "afname"]
    assert [(r["register"], r["kwh"]) for r in rijen] == [("nacht", "0.400000"), ("nacht", "0.000000"),
                                                         ("nacht", "0.250000")]
    # Belgische afspraak omgedraaid (tarief 1 = nacht) of een enkelvoudige meter.
    assert kwartierrijen(blokken, vanaf=T0, tot=T0 + KWARTIER, tarief_1="nacht")[0]["register"] == "dag"
    assert kwartierrijen(blokken, vanaf=T0, tot=T0 + KWARTIER, enkelvoudig=True)[0]["register"] == "enkelvoudig"


def test_onvolledig_kwartier_en_vanaf_midden_in_een_kwartier():
    blokken = _blokken({"afname_1": [0.1] * 6})
    assert len(kwartierrijen(blokken, vanaf=T0, tot=T0 + KWARTIER + timedelta(minutes=10))) == 2  # één kwartier
    assert kwartierrijen(blokken, vanaf=T0 + timedelta(minutes=7), tot=T0 + KWARTIER)[0]["tijdstip"] \
        == "2026-10-06T08:00:00+00:00"
    assert kwartiergrens(datetime(2026, 10, 6, 10, 29, 59, tzinfo=timezone(timedelta(hours=2)))) == \
        datetime(2026, 10, 6, 8, 15, tzinfo=timezone.utc)


def test_ontbrekende_blokken_tellen_als_nul_en_negatief_stopt():
    assert kwartierrijen({}, vanaf=T0, tot=T0 + KWARTIER)[0]["kwh"] == "0.000000"
    with pytest.raises(ValueError, match="negatief"):
        kwartierrijen(_blokken({"afname_1": [0.1, -0.2, 0]}), vanaf=T0, tot=T0 + KWARTIER)
    with pytest.raises(ValueError, match="onmogelijk"):
        kwartierrijen(_blokken({"afname_1": [10, 10, 10]}), vanaf=T0, tot=T0 + KWARTIER)


def test_float_ruis_verdwijnt():
    # 0.1 + 0.2 als float is 0.30000000000000004; via repr per blok en Decimal-som blijft het 0.3.
    assert kwartierrijen(_blokken({"afname_1": [0.1, 0.2, 0]}), vanaf=T0, tot=T0 + KWARTIER)[0]["kwh"] == "0.300000"


def test_stukken_splitsen_geen_kwartier():
    rijen = kwartierrijen({}, vanaf=T0, tot=T0 + 600 * KWARTIER)
    stukken = in_stukken(rijen)
    assert [len(s) for s in stukken] == [1000, 200]
    assert stukken[1][0]["tijdstip"] == (T0 + 500 * KWARTIER).isoformat()
    with pytest.raises(ValueError):
        in_stukken(rijen, grootte=999)


# --- gas (ADR-0064) ---------------------------------------------------------

def test_gas_twaalf_blokken_per_uur_in_m3():
    from p1 import UUR, gasrijen
    blokken = {T0 + i * BLOK: 0.01 for i in range(24)}
    blokken[T0 + 13 * BLOK] = 0.5
    assert gasrijen(blokken, vanaf=T0, tot=T0 + 2 * UUR) == [
        {"tijdstip": "2026-10-06T08:00:00+00:00", "m3": "0.120000"},
        {"tijdstip": "2026-10-06T09:00:00+00:00", "m3": "0.610000"},
    ]
    # Een onvolledig laatste uur gaat niet mee; een ontbrekend blok telt als 0.
    assert gasrijen({T0: 0.2}, vanaf=T0, tot=T0 + UUR + KWARTIER) == [
        {"tijdstip": "2026-10-06T08:00:00+00:00", "m3": "0.200000"}]


def test_gas_negatief_of_onmogelijk_stopt():
    from p1 import UUR, gasrijen
    with pytest.raises(ValueError, match="negatief"):
        gasrijen({T0: -0.1}, vanaf=T0, tot=T0 + UUR)
    with pytest.raises(ValueError, match="onmogelijk"):
        gasrijen({T0 + i * BLOK: 1.0 for i in range(12)}, vanaf=T0, tot=T0 + UUR)
