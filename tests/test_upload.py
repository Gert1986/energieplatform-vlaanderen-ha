"""
test_upload.py
~~~~~~~~~~~~~~

De HA-laag van de P1-upload (`custom_components/sef/__init__.py::_upload`)
met nep-statistieken en een nep-SEF. Vraagt `homeassistant` (zie README);
zonder wordt hij overgeslagen.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custom_components.sef as sef  # noqa: E402

NU = datetime(2026, 10, 6, 8, 47, 3, tzinfo=timezone.utc)


class NepClient:
    def __init__(self, laatste: str | None, laatste_gas: str | None = None):
        self.laatste, self.laatste_gas, self.posts = laatste, laatste_gas, []

    async def vraag(self, methode, pad, json=None):
        if methode == "GET":
            return {"aansluitingen": [{"id": 7, "energie": "elektriciteit", "laatste_meting": self.laatste},
                                      {"id": 8, "energie": "gas", "laatste_meting": self.laatste_gas}]}
        self.posts.append((pad, json))
        return {"status": "geschreven", "rijen": len(json["metingen"])}


@pytest.fixture
def statistiek(monkeypatch):
    """5-minutenblokken van 08:00 tot 08:40 (het blok van 08:40 is het laatste dat al bestaat)."""
    gevraagd = {}

    class Recorder:
        async def async_add_executor_job(self, functie, hass, start, eind, ids, periode, eenheden, soorten):
            gevraagd.update(start=start, eind=eind, ids=ids, periode=periode, soorten=soorten)
            begin = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
            rijen = [{"start": (begin + timedelta(minutes=5 * i)).timestamp(), "change": 0.1} for i in range(9)]
            return {"sensor.t1": rijen, "sensor.e1": [{**r, "change": 0.0} for r in rijen],
                    "sensor.gas": [{**r, "change": 0.01} for r in rijen]}

    monkeypatch.setattr(sef, "get_instance", lambda hass: Recorder())
    monkeypatch.setattr(sef.dt_util, "utcnow", lambda: NU)
    monkeypatch.setattr(sef.persistent_notification, "async_dismiss", lambda hass, nid: None)
    return gevraagd


CONF = {"meter": "5c2fafabcdef", "tarief_1": "dag", "enkelvoudig": False}
TELWERKEN = {"afname_1": "sensor.t1", "injectie_1": "sensor.e1"}


def test_upload_sluit_aan_en_stopt_bij_het_laatste_volledige_kwartier(statistiek):
    client = NepClient("2026-10-06T07:45:00+00:00")
    asyncio.run(sef._upload(None, client, CONF, TELWERKEN))
    assert statistiek["start"] == datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
    assert statistiek["eind"] == datetime(2026, 10, 6, 8, 45, tzinfo=timezone.utc)
    assert (statistiek["periode"], statistiek["soorten"]) == ("5minute", {"change"})
    ((pad, body),) = client.posts
    assert pad == "/mij/aansluitingen/7/meetwaarden" and body["meter"] == "5c2fafabcdef"
    # Blokken 08:00-08:40: kwartieren 08:00, 08:15, 08:30 (dat van 08:30 is volledig, laatste blok 08:40).
    assert [(m["tijdstip"][11:16], m["richting"], m["kwh"]) for m in body["metingen"]][::2] == [
        ("08:00", "afname", "0.300000"), ("08:15", "afname", "0.300000"), ("08:30", "afname", "0.300000")]


def test_niets_te_doen_en_te_oude_meting(statistiek):
    client = NepClient("2026-10-06T08:30:00+00:00")  # al tot het lopende kwartier
    asyncio.run(sef._upload(None, client, CONF, TELWERKEN))
    assert client.posts == [] and statistiek == {}
    with pytest.raises(sef.SefFout) as fout:
        asyncio.run(sef._upload(None, NepClient("2026-09-25T21:45:00+00:00"), CONF, TELWERKEN))
    assert fout.value.code == "GAT_NA_LAATSTE_METING"
    with pytest.raises(sef.SefFout) as fout:
        asyncio.run(sef._upload(None, NepClient(None), CONF, TELWERKEN))
    assert fout.value.code == "GEEN_FLUVIUS_DATA"


def test_configuratieschema():
    goed = {"sef": {"sleutel": "sef_x",
                    "meter": "5c2fafabcdef", "afname_1": "sensor.p1_meter_energy_import_tariff_1"}}
    conf = sef.CONFIG_SCHEMA(goed)["sef"]
    assert (conf["tarief_1"], conf["enkelvoudig"], conf["url"]) == ("dag", False, "https://energieplatform-vlaanderen.be")
    for fout in ({"meter": "met spatie"}, {"tarief_1": "piek"}, {"afname_1": "geen entiteit"}):
        with pytest.raises(Exception):
            sef.CONFIG_SCHEMA({"sef": {**goed["sef"], **fout}})


def test_gasupload_stuurt_enkel_volledige_uren_in_m3(statistiek, monkeypatch):
    """ADR-0064: laatste gasmeting 07:00, nu 08:47: het uur van 08:00 loopt nog, niets te vragen of te sturen.
    Laatste gasmeting 06:00 met blokken 07:00-08:40: enkel het volle uur van 07:00, als m³."""
    client = NepClient("2026-10-06T07:45:00+00:00", "2026-10-06T07:00:00+00:00")
    asyncio.run(sef._upload_gas(None, client, {**CONF, "gas": "sensor.gas"}))
    assert client.posts == [] and statistiek == {}

    class Recorder:
        async def async_add_executor_job(self, functie, hass, start, eind, ids, periode, eenheden, soorten):
            begin = datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)
            return {"sensor.gas": [{"start": (begin + timedelta(minutes=5 * i)).timestamp(), "change": 0.01}
                                   for i in range(21)]}

    monkeypatch.setattr(sef, "get_instance", lambda hass: Recorder())
    client = NepClient("2026-10-06T07:45:00+00:00", "2026-10-06T06:00:00+00:00")
    asyncio.run(sef._upload_gas(None, client, {**CONF, "gas": "sensor.gas"}))
    ((pad, body),) = client.posts
    assert pad == "/mij/aansluitingen/8/meetwaarden"
    assert body == {"meter": "5c2fafabcdef", "metingen": [{"tijdstip": "2026-10-06T07:00:00+00:00", "m3": "0.120000"}]}
