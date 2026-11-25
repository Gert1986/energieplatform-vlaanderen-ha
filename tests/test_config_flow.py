"""
test_config_flow.py
~~~~~~~~~~~~~~~~~~~

Instellen via de UI, de YAML-import, de sensoren na de setup, de Repairs van
de uploads en het diagnosebestand, tegen een nep-SEF (`SefClient.vraag`). Vraagt `pytest-homeassistant-custom-component`.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import aiohttp
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant import config_entries  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers import issue_registry as ir

import custom_components.sef as sef  # noqa: E402

WAAKHOND = {
    "elektriciteit": {
        "contract": {"leverancier": "ENGIE", "product": "Easy", "van": "2026-05-01", "tot": "2027-05-01",
                     "dagen_tot_einde": 199},
        "vergelijking": {"id": "x", "afgerond_op": "2026-10-12T09:14:03+00:00", "periode_van": "2025-10-01",
                         "periode_tot": "2026-10-01"},
        "huidig": {"leverancier": "ENGIE", "product": "Easy", "totaal_eur": "1873.41", "gemiddelde_ct_kwh": "38.672"},
        "beste": {"vreg_id": "1", "leverancier": "Bolt", "product": "Vast", "contracttype": "vast",
                  "totaal_eur": "1761.02", "gemiddelde_ct_kwh": "36.351"},
        "besparing_eur": "112.39", "reden": None},
    "gas": {"contract": None, "vergelijking": None, "huidig": None, "beste": None, "besparing_eur": None,
            "reden": "Nog geen afgeronde vergelijking: start er een op de website."},
}
ANTWOORDEN = {
    "/mij/aansluitingen": {"aansluitingen": []},
    "/mij/prijs": {"leverancier": "ENGIE", "product": "Easy", "contracttype": "vast", "zone_nu": "dag",
                   "vreg_id": "77", "tariefkaart_url": "https://engie.be/kaart.pdf",
                   "afname_nu": "0.350406532", "injectie_nu": "0.04161", "waarschuwingen": [],
                   "kwartieren": [{"tijdstip": "2026-10-14T00:00:00+02:00", "zone": "nacht", "spot_eur_mwh": "80",
                                   "afname": "0.31", "injectie": "0.04", "beurs_allin": "0.2085"}],
                   # Sinds 0.13.0; de maand van nu, zodat sensor.sef_maandpiek ze vindt.
                   "capaciteit": {"eenheid": "EUR incl. btw", "tarief_eur_kw_jaar": "53.0", "ondergrens_kw": "2.5",
                                  "jaar_eur": "28.4875", "maanden": [
                                      {"maand": "2025-12", "piek_kw": "1.9", "kost_eur": "11.0416666667"},
                                      {"maand": f"{datetime.now():%Y-%m}", "piek_kw": "3.95",
                                       "kost_eur": "17.4458333333"}]}},
    "/mij/waakhond": WAAKHOND,
    "/mij/voorschot": {"eenheid": "EUR incl. btw", "aansluitingen": [
        {"aansluiting": 1, "energie": "elektriciteit", "contract": "ENGIE Easy", "voorschot_eur_maand": "93.00",
         "reden": None,
         "maand": {"van": "2026-10-01", "tot": "2026-10-17", "kost_eur": "51.20", "reden": None, "waarschuwingen": [],
                   "voorschot_eur": "48", "verschil_eur": "3.20"},
         "vorige_maand": {"van": "2026-09-01", "tot": "2026-10-01", "kost_eur": "88.40", "reden": None,
                          "waarschuwingen": [], "voorschot_eur": "93.00", "verschil_eur": "-4.60"},
         "jaar": {"van": "2025-10-01", "tot": "2026-10-01", "kost_eur": "1234.56", "reden": None, "waarschuwingen": [],
                  "voorschot_eur": "1116.00", "saldo_eur": "118.56", "aangeraden_voorschot_eur": "103",
                  "normaal": None},
         "recent": {"van": "2026-10-03", "tot": "2026-10-17", "kost_eur": "42.00", "reden": None, "waarschuwingen": []},
         "recent_vorig_jaar": {"van": "2025-10-04", "tot": "2025-10-18", "kost_eur": "60.00", "reden": None,
                               "waarschuwingen": []},
         "sinds_afrekening": {"van": "2026-05-01", "tot": "2026-10-17", "kost_eur": "512.30", "reden": None,
                              "waarschuwingen": [], "voorschot_eur": "513.00", "verschil_eur": "-0.70"}},
        {"aansluiting": 2, "energie": "gas", "contract": "ENGIE Easy", "voorschot_eur_maand": "100.00", "reden": None,
         "maand": None, "vorige_maand": {"van": "2026-09-01", "tot": "2026-10-01", "kost_eur": "24.56", "reden": None,
                                         "waarschuwingen": [], "voorschot_eur": "100.00", "verschil_eur": "-75.44"},
         "jaar": {"van": "2025-10-01", "tot": "2026-10-01", "kost_eur": "1241.62", "reden": None, "waarschuwingen": [],
                  "voorschot_eur": "1200.00", "saldo_eur": "41.62", "aangeraden_voorschot_eur": "113",
                  "normaal": {"kost_eur": "1350.10", "saldo_eur": "150.10", "correctie_eur": "108.48",
                              "extra_kwh": "986.2", "graaddagen": "2200.5", "graaddagen_normaal": "2420.0",
                              "station": 6434, "referentie_van": 2015, "referentie_tot": 2025, "referentie_jaren": 9,
                              "reden": None}},
         "sinds_afrekening": {"van": None, "tot": None, "kost_eur": None, "voorschot_eur": None, "verschil_eur": None,
                              "reden": "geef de datum van uw laatste afrekening in bij Mijn voorschot.",
                              "waarschuwingen": []}}]},
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
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"meter": "abcd1234",
                                                                      "batterij_laden": "sensor.batterij_in"})
    assert r["errors"] == {"base": "batterij_paar"}  # ADR-0066: beide batterijtelwerken of geen
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"meter": "abcd1234",
                                                                      "batterij_soc": "sensor.batterij_soc"})
    assert r["errors"] == {"base": "planner_drie"}  # 0.20.0: laadtoestand, capaciteit en vermogen samen
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
    gemiddeld = hass.states.get("sensor.sef_gemiddelde_prijs_elektriciteit")  # sinds 0.11.0
    assert (gemiddeld.state, gemiddeld.attributes["unit_of_measurement"]) == ("38.672", "c€/kWh")
    assert (gemiddeld.attributes["beste_contract"], gemiddeld.attributes["beste_gemiddelde_prijs"]) == ("Bolt Vast", 36.351)
    contract = hass.states.get("sensor.sef_contract_elektriciteit")
    assert contract.state == "ENGIE Easy" and contract.attributes["tariefkaart_url"] == "https://engie.be/kaart.pdf"
    assert contract.attributes["vreg_id"] == "77"
    assert hass.states.get("sensor.sef_besparing_gas") is None
    beurs = hass.states.get("sensor.sef_beursprijs_all_in")  # sinds 0.13.0, zelfde voet als de afnameprijs
    assert beurs.attributes["kwartieren"] == [{"start": "2026-10-14T00:00:00+02:00", "prijs": 0.2085}]
    piek = hass.states.get("sensor.sef_maandpiek")
    assert (piek.state, piek.attributes["kost_eur"], len(piek.attributes["maanden"])) == ("3.95", 17.45, 2)
    hoogste = hass.states.get("sensor.sef_hoogste_piek")
    assert (hoogste.state, hoogste.attributes["maand"]) == ("3.95", f"{datetime.now():%Y-%m}")
    capaciteit = hass.states.get("sensor.sef_capaciteitstarief")
    assert capaciteit.state == "28.4875" and capaciteit.attributes["gemiddelde_piek_kw"] == 3.225  # 1,9 telt als 2,5
    maand = hass.states.get("sensor.sef_kost_deze_maand_elektriciteit")
    assert (maand.state, maand.attributes["voorschot_tot_nu"], maand.attributes["verschil"]) == ("51.2", 48.0, 3.2)
    assert (maand.attributes["laatste_14_dagen_kost"], maand.attributes["zelfde_dagen_vorig_jaar_kost"]) == (42.0, 60.0)
    assert hass.states.get("sensor.sef_kost_deze_maand_gas").attributes["laatste_14_dagen_kost"] is None
    jaar = hass.states.get("sensor.sef_jaarschatting_elektriciteit")
    assert (jaar.state, jaar.attributes["aangeraden_voorschot"]) == ("1234.56", 103.0)
    assert hass.states.get("sensor.sef_voorschot_saldo_elektriciteit").state == "118.56"
    # Gas staat in SEF (aansluiting 2): ook zonder gastelwerk de voorschotsensoren, met het normale jaar.
    gasjaar = hass.states.get("sensor.sef_jaarschatting_gas")
    assert (gasjaar.state, gasjaar.attributes["normaal_kost"], gasjaar.attributes["referentie"]) == \
        ("1241.62", 1350.1, "2015-2025")
    assert gasjaar.attributes["referentie_jaren"] == 9
    assert hass.states.get("sensor.sef_voorschot_saldo_gas").attributes["normaal_saldo"] == 150.1
    sinds = hass.states.get("sensor.sef_kost_sinds_afrekening_elektriciteit")  # sinds 0.9.0
    assert (sinds.state, sinds.attributes["van"], sinds.attributes["tot"]) == ("512.3", "2026-05-01", "2026-10-17")
    assert (sinds.attributes["voorschot"], sinds.attributes["verschil"], sinds.attributes["reden"]) == (513.0, -0.7, None)
    gas_sinds = hass.states.get("sensor.sef_kost_sinds_afrekening_gas")
    assert gas_sinds.state == "unknown" and "afrekening" in gas_sinds.attributes["reden"]
    assert jaar.attributes["normaal_kost"] is None  # elektriciteit: geen weercorrectie
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
    r = await hass.config_entries.flow.async_init(  # B1: de YAML blijft de bron
        sef.DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "yaml_beheerd"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_wisselt_telwerken_zonder_andere_entiteiten(recorder_mock, enable_custom_integrations, hass,
                                                                       nep_sef):
    """Sinds 0.17.0: telwerken of sleutel wijzigen zonder de integratie te verwijderen. Een leeggemaakt
    telwerk verdwijnt, een lege sleutel houdt de huidige, en de entiteiten houden hun unique_id."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=sef.DOMAIN, unique_id=sef.DOMAIN, title="SEF", source=config_entries.SOURCE_USER,
                            data={"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False,
                                  "meter": "abcd1234", "afname_1": "sensor.p1_afname_1"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def entiteiten():
        return {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    voor = entiteiten()

    r = await entry.start_reconfigure_flow(hass)
    assert r["step_id"] == "reconfigure"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://x", "sleutel": "sef_fout"})
    assert r["errors"] == {"base": "ongeldige_sleutel"} and entry.data["sleutel"] == "sef_goed"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": "https://x"})
    assert r["step_id"] == "meter"
    suggesties = {str(k): k.description["suggested_value"] for k in r["data_schema"].schema
                  if k.description and "suggested_value" in k.description}
    assert (suggesties["meter"], suggesties["afname_1"]) == ("abcd1234", "sensor.p1_afname_1")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"meter": "abcd1234",
                                                                      "afname_2": "sensor.p1_afname_2"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert dict(entry.data) == {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False,
                                "meter": "abcd1234", "afname_2": "sensor.p1_afname_2"}
    assert entiteiten() == voor and hass.states.get("sensor.sef_afnameprijs").state == "0.350406532"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnose_zonder_geheimen_of_bedragen(recorder_mock, enable_custom_integrations, hass, nep_sef):
    """Het diagnosebestand belandt vaak in een publiek issue: geen sleutel, meter-ID, bedrag, eigen adres of
    entity-ID van een telwerk erin. Het volledige bestand, zodat een nieuw veld niet ongezien meekomt."""
    from homeassistant.const import __version__ as HA_VERSIE
    from homeassistant.loader import async_get_integration

    from custom_components.sef.diagnostics import async_get_config_entry_diagnostics
    yaml = {"url": "https://thuis.tail1234.ts.net", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False,
            "meter": "abcd1234", "afname_1": "sensor.gert_p1_afname", "vermogen": "sensor.gert_p1_vermogen",
            "batterij_soc": "sensor.gert_batterij_soc", "batterij_capaciteit": 5.12, "batterij_vermogen": 2500}
    await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=yaml)
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries(sef.DOMAIN)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    for geheim in ("sef_goed", "abcd1234", "0.350406532", "1873.41", "ENGIE", "tail1234", "gert"):
        assert geheim not in str(diag), geheim
    verborgen = "**REDACTED**"
    versie = str((await async_get_integration(hass, sef.DOMAIN)).version)
    assert diag == {
        "versie": versie, "ha_versie": HA_VERSIE,
        "instellingen": {"url": verborgen, "sleutel": verborgen, "tarief_1": "dag", "enkelvoudig": False,
                         "meter": verborgen, "afname_1": verborgen, "vermogen": verborgen,
                         "batterij_soc": verborgen, "batterij_capaciteit": 5.12, "batterij_vermogen": 2500},
        "uploads": {"P1-upload": {"laatste_poging": None, "laatste_succes": None, "fout": None}},
        "opvragingen": OPVRAGINGEN,
    }

    # Een netwerkfout noemt het adres ("Cannot connect to host …"): enkel het soort fout komt erin.
    async def onbereikbaar(self, methode, pad, json=None):
        raise aiohttp.ClientConnectionError("Cannot connect to host thuis.tail1234.ts.net:443")

    prijs = hass.data[sef.DOMAIN][entry.entry_id]["coordinators"]["SEF prijs"]
    with patch.object(sef.SefClient, "vraag", onbereikbaar):
        await prijs.async_refresh()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["opvragingen"]["SEF prijs"]["fout"] == "ClientConnectionError" and "tail1234" not in str(diag)


def _velden(pad: str, interval_s: float) -> dict:
    data = ANTWOORDEN[pad]
    return {"gelukt": True, "fout": None, "interval_s": interval_s, "velden": sorted(data),
            "kwartieren": len(data.get("kwartieren") or []), "waarschuwingen": data.get("waarschuwingen", [])}


OPVRAGINGEN = {"SEF prijs": _velden("/mij/prijs", 900.0), "SEF waakhond": _velden("/mij/waakhond", 3600.0),
               "SEF voorschot": _velden("/mij/voorschot", 3600.0)}


async def test_repair_bij_een_weigering_weg_na_de_volgende_geslaagde_upload(hass):
    """Sinds 0.16.0: wat de gebruiker moet oplossen (403, een onbekende 4xx-code, een telwerk zonder
    statistiek) is een Repair; een tijdelijke fout (5xx, 429, netwerk) enkel het logboek."""
    fouten = [sef.SefFout(403, "P1_GEEN_TOESTEMMING", "Zet de P1-stroom aan.")]

    async def upload():
        if fouten:
            raise fouten.pop(0)

    uploads = {}
    taak = sef._taak(hass, uploads, "sef_p1", "P1-upload", upload)
    register = ir.async_get(hass)
    await taak()
    issue = register.async_get_issue(sef.DOMAIN, "sef_p1")
    assert (issue.translation_key, issue.translation_placeholders) == (
        "upload_geweigerd", {"naam": "P1-upload", "code": "P1_GEEN_TOESTEMMING", "titel": "Zet de P1-stroom aan."})
    assert uploads["P1-upload"]["fout"] == "403 P1_GEEN_TOESTEMMING" and uploads["P1-upload"]["laatste_succes"] is None

    # Tijdelijk: geen nieuwe Repair, en een bestaande blijft tot het echt lukt.
    for tijdelijk in (sef.SefFout(503, "?", ""), sef.SefFout(429, "TE_VEEL", "Te veel aanvragen."),
                      aiohttp.ClientConnectionError()):
        fouten.append(tijdelijk)
        await taak()
        assert register.async_get_issue(sef.DOMAIN, "sef_p1").translation_placeholders["code"] == "P1_GEEN_TOESTEMMING"
    assert uploads["P1-upload"]["fout"] == "NETWERK"

    await taak()
    assert register.async_get_issue(sef.DOMAIN, "sef_p1") is None
    assert uploads["P1-upload"]["fout"] is None
    assert uploads["P1-upload"]["laatste_succes"] == uploads["P1-upload"]["laatste_poging"] is not None

    fouten.append(sef.SefFout(503, "?", ""))
    await taak()
    assert register.async_get_issue(sef.DOMAIN, "sef_p1") is None

    # Een code die de integratie nog niet kent, verdwijnt niet stil; een telwerkfout krijgt een eigen tekst.
    fouten.append(sef.SefFout(422, "NIEUWE_CODE", "Iets nieuws."))
    await taak()
    assert register.async_get_issue(sef.DOMAIN, "sef_p1").translation_placeholders["code"] == "NIEUWE_CODE"
    fouten.append(ValueError("geen 5-minutenstatistieken voor sensor.t1"))
    await taak()
    issue = register.async_get_issue(sef.DOMAIN, "sef_p1")
    assert issue.translation_key == "telwerkfout" and "sensor.t1" in issue.translation_placeholders["fout"]


async def test_prijs_springt_op_de_kwartiergrens(recorder_mock, enable_custom_integrations, hass, nep_sef, freezer):
    """Sinds 0.15.1: de nieuwe kwartierprijs staat er op de kwartiergrens, niet pas bij de volgende
    opvraging (die loopt per 15 min vanaf het opstarten, hier niet binnen de 10 min)."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    freezer.move_to("2026-10-14T10:05:00+00:00")
    prijs = ANTWOORDEN["/mij/prijs"] | {"kwartieren": [
        {"tijdstip": "2026-10-14T10:00:00+00:00", "afname": "0.30", "injectie": "0.04", "spot_eur_mwh": "80"},
        {"tijdstip": "2026-10-14T10:15:00+00:00", "afname": "0.40", "injectie": "0.05", "spot_eur_mwh": "120"}]}
    with patch.dict(ANTWOORDEN, {"/mij/prijs": prijs}):
        yaml = {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False}
        await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT},
                                                  data=yaml)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.sef_afnameprijs").state == "0.3"
        freezer.move_to("2026-10-14T10:15:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.sef_afnameprijs").state == "0.4"
        assert hass.states.get("sensor.sef_spotprijs").state == "120.0"


async def test_prijssignalen_springen_op_de_kwartiergrens(recorder_mock, enable_custom_integrations, hass, nep_sef,
                                                         freezer):
    """Sinds 0.18.0: rang, goedkoop/duur nu en het goedkoopste uur, lokaal uit de kwartieren."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    freezer.move_to("2026-10-14T10:05:00+00:00")
    afname = ["0.20", "0.40", "0.30", "0.50", "0.10"]  # 10:00 tot 11:15 UTC
    prijs = ANTWOORDEN["/mij/prijs"] | {"kwartieren": [
        {"tijdstip": f"2026-10-14T{10 + i // 4}:{15 * (i % 4):02d}:00+00:00", "afname": a, "injectie": "0.04"}
        for i, a in enumerate(afname)]}
    with patch.dict(ANTWOORDEN, {"/mij/prijs": prijs}):
        yaml = {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False}
        await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT},
                                                  data=yaml)
        await hass.async_block_till_done()
        rang = hass.states.get("sensor.sef_prijsrang")
        assert (rang.state, rang.attributes["prijsniveaus"], rang.attributes["horizon_tot"]) == \
            ("20", 5, "2026-10-14T11:15:00+00:00")  # enkel 0.10 is goedkoper: 1 van 5
        assert hass.states.get("binary_sensor.sef_goedkoop_nu").state == "on"
        assert hass.states.get("binary_sensor.sef_duur_nu").state == "off"
        uur = hass.states.get("sensor.sef_goedkoopste_1u")  # 10:15-11:15 gemiddeld 0.325, 10:00-11:00 0.35
        assert (uur.state, uur.attributes["einde"], uur.attributes["gemiddelde_prijs"]) == \
            ("2026-10-14T10:15:00+00:00", "2026-10-14T11:15:00+00:00", 0.325)
        assert hass.states.get("sensor.sef_goedkoopste_2u").state == "unknown"  # te weinig prijzen

        freezer.move_to("2026-10-14T10:15:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.sef_prijsrang").state == "50"  # 0.40: 0.30 en 0.10 goedkoper, van 4
        assert hass.states.get("binary_sensor.sef_goedkoop_nu").state == "off"
        freezer.move_to("2026-10-14T10:30:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.sef_goedkoopste_1u").state == "2026-10-14T10:15:00+00:00"  # loopt: blijft


async def test_peak_guard_rekent_elke_minuut_en_is_onbeschikbaar_zonder_telwerk(recorder_mock,
                                                                               enable_custom_integrations, hass,
                                                                               nep_sef, freezer):
    """Sinds 0.19.0: het kwartiervermogen uit het afnametelwerk, de verwachte piek met het netvermogen (W), de
    drempel (hier de ondergrens: SEF heeft deze maand nog geen piek) en de kost. Onbeschikbaar telwerk: alle
    piekentiteiten onbeschikbaar, nooit piekrisico aan."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async def op(tijd: str) -> None:
        freezer.move_to(tijd)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    freezer.move_to("2026-10-14T09:58:00+00:00")
    hass.states.async_set("sensor.p1_afname_1", "100", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.p1_vermogen", "2000", {"unit_of_measurement": "W"})
    yaml = {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False,
            "afname_1": "sensor.p1_afname_1", "vermogen": "sensor.p1_vermogen"}
    await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=yaml)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sef_kwartiervermogen").state == "unknown"  # gestart midden in een kwartier

    await op("2026-10-14T10:00:00+00:00")
    hass.states.async_set("sensor.p1_afname_1", "100.25", {"unit_of_measurement": "kWh"})
    await op("2026-10-14T10:05:00+00:00")
    # HA vuurt een tijdtrigger tot 0,5 s na de minuut (ook in productie): bij benadering.
    assert float(hass.states.get("sensor.sef_kwartiervermogen").state) == pytest.approx(3, abs=0.01)  # 0,25 kWh, 5 min
    assert float(hass.states.get("sensor.sef_verwachte_kwartierpiek").state) == pytest.approx(2.333, abs=0.01)
    assert hass.states.get("binary_sensor.sef_piekrisico").state == "off"

    hass.states.async_set("sensor.p1_vermogen", "6000", {"unit_of_measurement": "W"})
    await op("2026-10-14T10:05:30+00:00")  # geen minuut: niets herrekend
    await op("2026-10-14T10:06:00+00:00")
    risico = hass.states.get("binary_sensor.sef_piekrisico")
    assert risico.state == "on" and (risico.attributes["drempel"], risico.attributes["bron_drempel"]) == (2.5, "ondergrens")
    assert hass.states.get("sensor.sef_piekruimte").state.startswith("-")
    assert float(hass.states.get("sensor.sef_kost_nieuwe_piek").state) > 0

    hass.states.async_set("sensor.p1_afname_1", "unavailable")
    await op("2026-10-14T10:07:00+00:00")
    for entiteit in ("sensor.sef_kwartiervermogen", "sensor.sef_verwachte_kwartierpiek", "sensor.sef_piekruimte",
                     "sensor.sef_kost_nieuwe_piek", "binary_sensor.sef_piekrisico"):
        assert hass.states.get(entiteit).state == "unavailable", entiteit


async def test_batterijplanner_proefdraai_met_beknopt_logboek(recorder_mock, enable_custom_integrations, hass,
                                                             nep_sef, freezer):
    """Sinds 0.20.0: de modus blijft na een herstart, de beslissing heeft geen eenheid (dus in het logboek) en
    wisselt enkel van tekst als de actie of reden wisselt. Er wordt niets gestuurd."""
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import (async_capture_events, async_fire_time_changed,
                                                              mock_restore_cache)

    async def op(tijd: str) -> None:
        freezer.move_to(tijd)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    freezer.move_to("2026-10-14T09:58:00+00:00")
    mock_restore_cache(hass, [State("select.sef_modus", "proefdraai")])
    hass.states.async_set("sensor.p1_afname_1", "100", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.batterij_soc", "90", {"unit_of_measurement": "%"})
    oproepen = async_capture_events(hass, "call_service")
    gebeurd = async_capture_events(hass, "sef_beslissing")
    afname = ["0.05", "0.40", "0.30", "0.30"]  # 10:00 tot 11:00 UTC
    prijs = ANTWOORDEN["/mij/prijs"] | {"kwartieren": [
        {"tijdstip": f"2026-10-14T10:{15 * i:02d}:00+00:00", "afname": a, "injectie": "0.04"} for i, a in enumerate(afname)]}
    with patch.dict(ANTWOORDEN, {"/mij/prijs": prijs}):
        yaml = {"url": "https://x", "sleutel": "sef_goed", "tarief_1": "dag", "enkelvoudig": False,
                "afname_1": "sensor.p1_afname_1", "batterij_soc": "sensor.batterij_soc", "batterij_capaciteit": 5,
                "batterij_vermogen": 2500}
        await hass.config_entries.flow.async_init(sef.DOMAIN, context={"source": config_entries.SOURCE_IMPORT},
                                                  data=yaml)
        await hass.async_block_till_done()
        assert hass.states.get("select.sef_modus").state == "proefdraai"  # hersteld
        beslissing = hass.states.get("sensor.sef_beslissing")
        assert beslissing.state == "Zelfverbruik: geen prijs"  # 09:45 heeft geen prijs
        assert "unit_of_measurement" not in beslissing.attributes and "state_class" not in beslissing.attributes

        await op("2026-10-14T10:00:00+00:00")
        hass.states.async_set("sensor.p1_afname_1", "100.0083", {"unit_of_measurement": "kWh"})  # ±0,5 kW
        await op("2026-10-14T10:01:00+00:00")
        beslissing = hass.states.get("sensor.sef_beslissing")
        assert beslissing.state == "Zou laden: goedkoop kwartier"
        assert 1900 < beslissing.attributes["vermogen_w"] < 2100  # piekruimte tot de ondergrens 2,5 kW
        gewijzigd = beslissing.last_changed

        hass.states.async_set("sensor.p1_afname_1", "100.03", {"unit_of_measurement": "kWh"})
        await op("2026-10-14T10:02:00+00:00")
        beslissing = hass.states.get("sensor.sef_beslissing")
        assert beslissing.state == "Zou laden: goedkoop kwartier" and beslissing.last_changed == gewijzigd
        assert beslissing.attributes["vermogen_w"] < 1900  # meer afname, minder ruimte: enkel het attribuut

        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.sef_modus", "option": "monitor"}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.sef_beslissing").state == "Monitor"
    assert [e.data["reden"] for e in gebeurd] == ["geen prijs", "goedkoop kwartier", "monitor"]
    assert {e.data["domain"] for e in oproepen} == {"select"}  # enkel de eigen modus: niets gestuurd
