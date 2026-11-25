"""
config_flow.py
~~~~~~~~~~~~~~

Instellen via de UI: eerst adres en API-sleutel (nagekeken met
`GET /mij/aansluitingen`), dan optioneel de P1-meter, zijn telwerken en die
van de thuisbatterij. Sinds 0.17.0 kan dat later opnieuw (*Opnieuw
configureren*), zonder de integratie te verwijderen. Een bestaande
YAML-configuratie wordt bij het opstarten ingelezen en blijft daarna de bron
(een wijziging in de YAML werkt de ingave bij); zo'n ingave configureer je dus
in de YAML, niet in de UI.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN, METER_PATROON, STANDAARD_URL, SefClient, SefFout

_TELWERK = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
# Ingevulde waarden gaan als suggestie mee (`add_suggested_values_to_schema`), niet als `default`: een default
# komt terug als je het veld leegmaakt, en dan kan een telwerk nooit weg.
_METER = vol.Schema({
    vol.Optional("meter"): str,
    vol.Optional("afname_1"): _TELWERK,
    vol.Optional("afname_2"): _TELWERK,
    vol.Optional("injectie_1"): _TELWERK,
    vol.Optional("injectie_2"): _TELWERK,
    vol.Optional("tarief_1", default="dag"): selector.SelectSelector(
        selector.SelectSelectorConfig(options=["dag", "nacht"], translation_key="tarief_1")),
    vol.Optional("enkelvoudig", default=False): bool,
    vol.Optional("vermogen"): _TELWERK,            # Peak Guard, gaat niet naar SEF
    vol.Optional("gas"): _TELWERK,
    vol.Optional("batterij_laden"): _TELWERK,      # ADR-0066: beide of geen
    vol.Optional("batterij_ontladen"): _TELWERK,
    # Batterijplanner (proefdraai): alle drie of geen; maximale laadtoestand optioneel (standaard 100 %).
    vol.Optional("batterij_soc"): _TELWERK,
    vol.Optional("batterij_capaciteit"): selector.NumberSelector(selector.NumberSelectorConfig(
        min=0.1, max=100, step=0.01, unit_of_measurement="kWh", mode=selector.NumberSelectorMode.BOX)),
    vol.Optional("batterij_vermogen"): selector.NumberSelector(selector.NumberSelectorConfig(
        min=50, max=25000, step=50, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX)),
    vol.Optional("batterij_max_soc"): selector.NumberSelector(selector.NumberSelectorConfig(
        min=10, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)),
})


class SefConfigFlow(ConfigFlow, domain=DOMAIN):
    """Eén SEF-dossier per Home Assistant."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        fouten: dict[str, str] = {}
        if user_input is not None:
            fout = await self._controleer(user_input["url"], user_input["sleutel"])
            if fout is None:
                self._data = dict(user_input)
                return await self.async_step_meter()
            fouten["base"] = fout
        return self.async_show_form(step_id="user", errors=fouten, data_schema=vol.Schema({
            vol.Required("url", default=(user_input or {}).get("url", STANDAARD_URL)): str,
            vol.Required("sleutel"): str,
        }))

    async def async_step_meter(self, user_input: dict | None = None) -> ConfigFlowResult:
        """P1-meter en telwerken; alles leeg = enkel de prijs- en waakhondsensoren."""
        fouten: dict[str, str] = {}
        if user_input is not None:
            meter = user_input.get("meter", "").strip()
            telwerken = [k for k in ("afname_1", "afname_2", "injectie_1", "injectie_2", "gas", "batterij_laden",
                                     "batterij_ontladen") if user_input.get(k)]
            if meter and not METER_PATROON.fullmatch(meter):
                fouten["meter"] = "meter_ongeldig"
            elif telwerken and not meter:
                fouten["meter"] = "meter_nodig"
            elif bool(user_input.get("batterij_laden")) != bool(user_input.get("batterij_ontladen")):
                fouten["base"] = "batterij_paar"
            elif len({bool(user_input.get(k)) for k in ("batterij_soc", "batterij_capaciteit", "batterij_vermogen")}) > 1:
                fouten["base"] = "planner_drie"
            else:
                # Opnieuw opgebouwd uit adres, sleutel en dit formulier: een leeggemaakt telwerk verdwijnt.
                data = {**self._data, **{k: v for k, v in user_input.items() if v not in ("", None)}}
                if meter:
                    data["meter"] = meter
                if self.source == SOURCE_RECONFIGURE:
                    return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=data)
                return self.async_create_entry(title="SEF", data=data)
        if user_input is None and self.source == SOURCE_RECONFIGURE:
            user_input = dict(self._get_reconfigure_entry().data)
        return self.async_show_form(step_id="meter", errors=fouten,
                                    data_schema=self.add_suggested_values_to_schema(_METER, user_input or {}))

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Adres, sleutel en daarna de meterstap opnieuw, met de huidige waarden ingevuld. Een lege sleutel
        houdt de huidige (die staat nooit leesbaar in het formulier). Een ingave uit de YAML blijft door de
        YAML beheerd (B1): geen tweede bron die de YAML bij de volgende start stil terugzet."""
        entry = self._get_reconfigure_entry()
        if entry.source == SOURCE_IMPORT:
            return self.async_abort(reason="yaml_beheerd")
        fouten: dict[str, str] = {}
        if user_input is not None:
            sleutel = user_input.get("sleutel") or entry.data["sleutel"]
            fout = await self._controleer(user_input["url"], sleutel)
            if fout is None:
                self._data = {"url": user_input["url"], "sleutel": sleutel}
                return await self.async_step_meter()
            fouten["base"] = fout
        schema = vol.Schema({vol.Required("url", default=STANDAARD_URL): str, vol.Optional("sleutel"): str})
        vorige = {"url": (user_input or entry.data).get("url", STANDAARD_URL)}
        return self.async_show_form(step_id="reconfigure", errors=fouten,
                                    data_schema=self.add_suggested_values_to_schema(schema, vorige))

    async def async_step_import(self, import_data: dict) -> ConfigFlowResult:
        """De YAML (`sef:` in configuration.yaml): nieuw, of de bestaande ingave
        vervangen. Vervangen, niet aanvullen: een sleutel die uit de YAML
        verdwijnt (bv. `gas`), verdwijnt ook uit de ingave."""
        await self.async_set_unique_id(DOMAIN)
        for entry in self._async_current_entries():
            if dict(entry.data) != dict(import_data):
                self.hass.config_entries.async_update_entry(entry, data=dict(import_data))
                self.hass.async_create_task(self.hass.config_entries.async_reload(entry.entry_id))
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(title="SEF", data=dict(import_data))

    async def _controleer(self, url: str, sleutel: str) -> str | None:
        """None als de sleutel het eigen dossier mag lezen, anders een foutcode."""
        try:
            await SefClient(async_get_clientsession(self.hass), url, sleutel).vraag("GET", "/mij/aansluitingen")
        except SefFout as fout:
            return "ongeldige_sleutel" if fout.status in (401, 403) else "onverwacht"
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return "niet_bereikbaar"
        return None
