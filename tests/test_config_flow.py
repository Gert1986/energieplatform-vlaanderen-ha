"""
test_config_flow.py
~~~~~~~~~~~~~~~~~~~

Instellen via de UI, de YAML-import en de sensoren na de setup, tegen een
nep-SEF (`SefClient.vraag`). Vraagt `pytest-homeassistant-custom-component`.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

import custom_components.sef as sef  # noqa: E402

WAAKHOND = {
    "elektriciteit": {
        "contract": {"leverancier": "ENGIE", "product": "Easy", "van": "2026-05-01", "tot": "2027-05-01",
                     "dagen_tot_einde": 199},
        "vergelijking": {"id": "x", "afgerond_op": "2026-10-12T09:14:03+00:00", "periode_van": "2025-10-01",
                         "periode_tot": "2026-10-01"},
        "huidig": {"leverancier": "ENGIE", "product": "Easy", "totaal_eur": "1873.41"},
        "beste": {"vreg_id": "1", "leverancier": "Bolt", "product": "Vast", "contracttype": "vast",
                  "totaal_eur": "1761.02"},
        "besparing_eur": "112.39", "reden": None},
    "gas": {"contract": None, "vergelijking": None, "huidig": None, "beste": None, "besparing_eur": None,
            "reden": "Nog geen afgeronde vergelijking: start er een op de website."},
}
ANTWOORDEN = {
    "/mij/aansluitingen": {"aansluitingen": []},
    "/mij/prijs": {"leverancier": "ENGIE", "product": "Easy", "contracttype": "vast", "zone_nu": "dag",
                   "vreg_id": "77", "tariefkaart_url": "https://engie.be/kaart.pdf",
                   "afname_nu": "0.350406532", "injectie_nu": "0.04161", "waarschuwingen": [],
                   "kwartieren": [{"tijdstip": "2026-10-14T00:00:00+02:00", "zone": "nacht", "spot_eur_mwh": None,
                                   "afname": "0.31", "injectie": "0.04"}]},
    "/mij/waakhond": WAAKHOND,
    "/mij/voorschot": {"eenheid": "EUR incl. btw", "aansluitingen": [
        {"aansluiting": 1, "energie": "elektriciteit", "contract": "ENGIE Easy", "voorschot_eur_maand": "93.00",
         "reden": None,
         "maand": {"van": "2026-10-01", "tot": "2026-10-17", "kost_eur": "51.20", "reden": None, "waarschuwingen": [],
                   "voorschot_eur": "48", "verschil_eur": "3.20"},
         "vorige_maand": {"van": "2026-09-01", "tot": "2026-10-01", "kost_eur": "88.40", "reden": None,
                          "waarschuwingen": [], "voorschot_eur": "93.00", "verschil_eur": "-4.60"},
         "jaar": {"van": "2025-10-01", "tot": "2026-10-01", "kost_eur": "1234.56", "reden": None, "waarschuwingen": [],
                  "voorschot_eur": "1116.00", "saldo_eur": "118.56", "aangeraden_voorschot_eur": "103"}}]},
}


async def _nep_vraag(self, methode, pad, json=None):
    if self._kop["Authorization"] != "Bearer sef_goed":
        raise sef.SefFout(401, "SLEUTEL_ONGELDIG", "Ongeldige sleutel.")
    return ANTWOORDEN[pad]


@pytest.fixture
def nep_sef():
    with patch.object(sef.SefClient, "vraag", _nep_vraag):
        yield


async def test_ui_flow_controleert_sleutel_en_meter(recorder_mock, enable_custom_integrations, hass, nep_sef):
    r = await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://x", "sleutel": "sef_fout"})
    assert r["errors"] == {"base": "ongeldige_sleutel"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://x", "sleutel": "sef_goed"})
    assert r["step_id"] == "meter"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"afname_1": "sensor.p1_afname_1"})
    assert r["errors"] == {"meter": "meter_nodig"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"meter": "5c2f-ab"})
    assert r["errors"] == {"meter": "meter_ongeldig"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert {k: r["data"][k] for k in ("url", "sleutel", "tarief_1", "enkelvoudig")} == \
        {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False}
    await hass.async_block_till_done()

    # Na de setup: prijs, besparing en dagen tot het einde; geen gas (niet ingesteld, niets in SEF).
    assert hass.states.get("sensor.sef_afnameprijs").state == "0.350406532"
    besparing = hass.states.get("sensor.sef_besparing_elektriciteit")
    assert (besparing.state, besparing.attributes["beste_contract"]) == ("112.39", "Bolt Vast")
    assert besparing.attributes["periode_van"] == "2025-10-01"
    assert hass.states.get("sensor.sef_contract_elektriciteit_dagen_tot_einde").state == "199"
    contract = hass.states.get("sensor.sef_contract_elektriciteit")
    assert contract.state == "ENGIE Easy" and contract.attributes["tariefkaart_url"] == "https://engie.be/kaart.pdf"
    assert contract.attributes["vreg_id"] == "77"
    assert hass.states.get("sensor.sef_besparing_gas") is None
    maand = hass.states.get("sensor.sef_kost_deze_maand_elektriciteit")
    assert (maand.state, maand.attributes["voorschot_tot_nu"], maand.attributes["verschil"]) == ("51.2", 48.0, 3.2)
    jaar = hass.states.get("sensor.sef_jaarschatting_elektriciteit")
    assert (jaar.state, jaar.attributes["aangeraden_voorschot"]) == ("1234.56", 103.0)
    assert hass.states.get("sensor.sef_voorschot_saldo_elektriciteit").state == "118.56"
    assert hass.states.get("sensor.sef_jaarschatting_gas") is None
    assert hass.states.get("sensor.sef_afnameprijs").attributes["kwartieren"] == [
        {"start": "2026-10-14T00:00:00+02:00", "prijs": 0.31}]

    # Tweede keer: al ingesteld.
    r = await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_yaml_import_maakt_en_werkt_bij(recorder_mock, enable_custom_integrations, hass, nep_sef):
    yaml = {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False, "meter": "abcd1234"}
    r = await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT},
                                                  data=yaml)
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    r = await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT},
                                                  data={k: v for k, v in yaml.items() if k != "meter"} | {"tarief_1": "nacht"})
    assert r["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries(sef.DOMAIN)
    assert entry.data["tarief_1"] == "nacht" and "meter" not in entry.data  # de YAML vervangt, vult niet aan
    assert await hass.config_entries.async_unload(entry.entry_id)
