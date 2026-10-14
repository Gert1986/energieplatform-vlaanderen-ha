"""
config_flow.py
~~~~~~~~~~~~~~

Instellen via de UI: eerst adres en API-sleutel (nagekeken met
`GET /mij/aansluitingen`), dan optioneel de P1-meter en zijn telwerken. Een
bestaande YAML-configuratie wordt bij het opstarten één keer ingelezen en
blijft daarna de bron (een wijziging in de YAML werkt de ingave bij).

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN, METER_PATROON, STANDAARD_URL, SefClient, SefFout

_TELWERK = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


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
            telwerken = [k for k in ("afname_1", "afname_2", "injectie_1", "injectie_2", "gas") if user_input.get(k)]
            if meter and not METER_PATROON.fullmatch(meter):
                fouten["meter"] = "meter_ongeldig"
            elif telwerken and not meter:
                fouten["meter"] = "meter_nodig"
            else:
                data = {**self._data, **{k: v for k, v in user_input.items() if v not in ("", None)}}
                if meter:
                    data["meter"] = meter
                return self.async_create_entry(title="SEF", data=data)
        return self.async_show_form(step_id="meter", errors=fouten, data_schema=vol.Schema({
            vol.Optional("meter"): str,
            vol.Optional("afname_1"): _TELWERK,
            vol.Optional("afname_2"): _TELWERK,
            vol.Optional("injectie_1"): _TELWERK,
            vol.Optional("injectie_2"): _TELWERK,
            vol.Optional("tarief_1", default="dag"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["dag", "nacht"], translation_key="tarief_1")),
            vol.Optional("enkelvoudig", default=False): bool,
            vol.Optional("gas"): _TELWERK,
        }))

    async def async_step_import(self, import_data: dict) -> ConfigFlowResult:
        """De YAML (`sef:` in configuration.yaml): nieuw, of de bestaande ingave bijwerken."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=dict(import_data))
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
