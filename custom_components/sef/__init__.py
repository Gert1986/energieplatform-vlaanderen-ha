"""
__init__.py
~~~~~~~~~~~

SEF in Home Assistant (ADR-0054, ADR-0064): prijssensoren uit `/mij/prijs` en
`/mij/gasprijs`, waakhondsensoren uit `/mij/waakhond`, elk kwartier de
P1-kwartierwaarden en elk uur het gasverbruik naar SEF. Instellen via de UI
(`config_flow.py`); een `sef:`-blok in de YAML wordt daarin ingelezen.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import aiohttp
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .p1 import BLOK, KANALEN, KWARTIER, UUR, gasrijen, in_stukken, kwartiergrens, kwartierrijen, uurgrens

_LOGGER = logging.getLogger(__name__)
DOMAIN = "sef"
PLATFORMS = ["sensor"]
MAX_DAGEN_TERUG = 10  # zoals SEF en de bewaartermijn van de korte-termijnstatistieken
STANDAARD_URL = "https://energieplatform-vlaanderen.be"
METER_PATROON = re.compile(r"[0-9A-Za-z]{4,64}")
STANDAARD = {"url": STANDAARD_URL, "tarief_1": "dag", "enkelvoudig": False}

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({
    vol.Optional("url", default=STANDAARD_URL): cv.url,  # enkel anders voor een test- of tailnet-adres
    vol.Required("sleutel"): cv.string,          # API-sleutel, best via !secret
    vol.Optional("meter"): vol.Match(METER_PATROON.pattern),
    vol.Optional("afname_1"): cv.entity_id,      # telwerken (kWh, total_increasing)
    vol.Optional("afname_2"): cv.entity_id,
    vol.Optional("injectie_1"): cv.entity_id,
    vol.Optional("injectie_2"): cv.entity_id,
    vol.Optional("tarief_1", default="dag"): vol.In(["dag", "nacht"]),
    vol.Optional("enkelvoudig", default=False): cv.boolean,
    vol.Optional("gas"): cv.entity_id,           # gastelwerk (m³, total_increasing): upload + gasprijssensor
})}, extra=vol.ALLOW_EXTRA)


class SefFout(Exception):
    """Een antwoord van SEF dat geen succes is; `code` uit Problem Details."""

    def __init__(self, status: int, code: str, titel: str) -> None:
        super().__init__(f"{status} {code}: {titel}")
        self.status, self.code, self.titel = status, code, titel


class SefClient:
    """Dunne client voor `/api/v1` met een Bearer-sleutel."""

    def __init__(self, sessie: aiohttp.ClientSession, url: str, sleutel: str) -> None:
        self._sessie, self._basis, self._kop = sessie, url.rstrip("/") + "/api/v1", {"Authorization": f"Bearer {sleutel}"}

    async def vraag(self, methode: str, pad: str, json: dict | None = None) -> dict:
        async with self._sessie.request(methode, self._basis + pad, json=json, headers=self._kop,
                                        timeout=aiohttp.ClientTimeout(total=60)) as antwoord:
            inhoud = await antwoord.json(content_type=None)
            if antwoord.status >= 400:
                raise SefFout(antwoord.status, (inhoud or {}).get("code", "?"), (inhoud or {}).get("title", ""))
            return inhoud


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Een `sef:`-blok in de YAML gaat naar de config flow (nieuw of bijwerken)."""
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(config[DOMAIN])))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start de sensoren en (met meter en telwerken) de upload elk kwartier en elk uur."""
    conf = {**STANDAARD, **entry.data}
    client = SefClient(async_get_clientsession(hass), conf["url"], conf["sleutel"])
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"client": client, "conf": conf}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    telwerken = {k: conf[k] for k in KANALEN if k in conf}
    if "meter" not in conf or not (telwerken or "gas" in conf):
        _LOGGER.info("SEF: geen meter of telwerken ingesteld, enkel prijs- en waakhondsensoren")
        return True
    if telwerken:
        kwartier = _taak(hass, "sef_p1", "P1-upload", lambda: _upload(hass, client, conf, telwerken))
        # Twee minuten na elk kwartier: de 5-minutenstatistiek van het laatste blok is dan klaar.
        entry.async_on_unload(async_track_time_change(hass, kwartier, minute=[2, 17, 32, 47], second=0))
        entry.async_on_unload(async_call_later(hass, 60, kwartier))
    if "gas" in conf:
        # Eigen taak en melding: een fout aan de gaskant stopt de elektriciteit niet (ADR-0064 §6).
        uur = _taak(hass, "sef_p1_gas", "P1-gasupload", lambda: _upload_gas(hass, client, conf))
        entry.async_on_unload(async_track_time_change(hass, uur, minute=7, second=0))
        entry.async_on_unload(async_call_later(hass, 90, uur))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Sensoren weg; de tijdstriggers stopt `async_on_unload`."""
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return ok


def _taak(hass: HomeAssistant, melding: str, naam: str, upload):
    """Een tijdsgebonden upload die nooit twee keer tegelijk loopt; fouten van
    SEF of een telwerk worden een melding (`melding` als id), een
    netwerkfout probeert het volgende venster opnieuw."""
    bezig = False

    async def taak(_now: datetime | None = None) -> None:
        nonlocal bezig
        if bezig:  # een aanvulling van dagen kan langer duren dan een kwartier
            return
        bezig = True
        try:
            await upload()
            persistent_notification.async_dismiss(hass, melding)
        except SefFout as fout:
            _LOGGER.error("SEF weigerde de %s: %s", naam, fout)
            persistent_notification.async_create(hass, f"SEF weigerde de {naam} ({fout.code}): {fout.titel}",
                                                 title="SEF", notification_id=melding)
        except (aiohttp.ClientError, TimeoutError) as fout:
            _LOGGER.warning("SEF niet bereikbaar, %s straks opnieuw: %s", naam, fout)
        except ValueError as fout:  # telwerkfout uit p1.py: niet stil doorgaan
            _LOGGER.error("%s: %s", naam, fout)
            persistent_notification.async_create(hass, f"{naam} gestopt: {fout}", title="SEF",
                                                 notification_id=melding)
        finally:
            bezig = False

    return taak


async def _venster(client: SefClient, energie: str, stap: timedelta, grens) -> tuple[dict, datetime, datetime] | None:
    """(aansluiting, vanaf, tot): van na de laatste meting in SEF tot de laatste
    grens (`grens`: kwartier- of uurgrens) vóór nu; None als er niets te doen is."""
    aansluitingen = [a for a in (await client.vraag("GET", "/mij/aansluitingen"))["aansluitingen"]
                     if a["energie"] == energie]
    if len(aansluitingen) != 1:
        raise SefFout(0, "AANSLUITING", f"{len(aansluitingen)} {energie}aansluitingen in SEF, verwacht één.")
    aansluiting = aansluitingen[0]
    nu = dt_util.utcnow()
    if not aansluiting["laatste_meting"]:
        raise SefFout(0, "GEEN_FLUVIUS_DATA", "Laad eerst een Fluvius-export op in SEF.")
    vanaf = dt_util.parse_datetime(aansluiting["laatste_meting"]) + stap
    if vanaf < nu - timedelta(days=MAX_DAGEN_TERUG):
        raise SefFout(0, "GAT_NA_LAATSTE_METING",
                      f"De meting in SEF stopt op {aansluiting['laatste_meting']}, langer dan {MAX_DAGEN_TERUG} "
                      "dagen geleden. Laad eerst een nieuwe Fluvius-export op.")
    tot = grens(nu)
    return None if vanaf >= tot else (aansluiting, vanaf, tot)


async def _blokken(hass: HomeAssistant, entiteiten: set[str], vanaf: datetime, tot: datetime) -> dict:
    """Per entiteit het verbruik per 5-minutenblok (begin -> `change`).
    Een telwerk zonder statistiek telt niet stil als 0: ValueError (melding in
    HA) als een ander telwerk in hetzelfde venster wel blokken heeft, of als er
    over een uur of meer geen enkel blok is. Een korter leeg venster is gewoon
    de recorder die nog niet bij is."""
    ruw = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, vanaf, tot, entiteiten, "5minute", None, {"change"})
    blokken = {e: {dt_util.utc_from_timestamp(r["start"]): r["change"] for r in ruw.get(e, [])
                   if r.get("change") is not None} for e in entiteiten}
    leeg = sorted(e for e, b in blokken.items() if not b)
    if leeg and (len(leeg) < len(blokken) or tot - vanaf >= UUR):
        raise ValueError(
            f"geen 5-minutenstatistieken voor {', '.join(leeg)} sinds {dt_util.as_local(vanaf):%d/%m %H:%M}. "
            "Bestaat die entiteit en is het een telwerk (kWh of m³, state_class total_increasing)? "
            "Kijk in Ontwikkelhulpmiddelen → Statistieken; bij HomeWizard staat het gastelwerk op een eigen "
            "apparaat (Gas meter).")
    return blokken


async def _upload_gas(hass: HomeAssistant, client: SefClient, conf: dict) -> None:
    """Het gasverbruik in m³ van elk volledig uur sinds de laatste gasmeting in
    SEF (ADR-0064); SEF rekent om naar kWh."""
    venster = await _venster(client, "gas", UUR, uurgrens)
    if venster is None:
        return
    aansluiting, vanaf, tot = venster
    blokken = (await _blokken(hass, {conf["gas"]}, vanaf, tot))[conf["gas"]]
    if not blokken:
        return
    # Enkel uren waarvan het laatste blok al gecompileerd is: geen POST zonder nieuw uur (limiet).
    tot = min(tot, uurgrens(max(blokken) + BLOK))
    rijen = gasrijen(blokken, vanaf=vanaf, tot=tot)
    for stuk in in_stukken(rijen):
        uitslag = await client.vraag("POST", f"/mij/aansluitingen/{aansluiting['id']}/meetwaarden",
                                     json={"meter": conf["meter"], "metingen": stuk})
        _LOGGER.debug("SEF P1-gasupload: %s", {k: uitslag.get(k) for k in ("status", "rijen", "nieuw", "fluvius_behouden")})


async def _upload(hass: HomeAssistant, client: SefClient, conf: dict, telwerken: dict[str, str]) -> None:
    """Alles sinds de laatste meting in SEF, tot het laatste volledige kwartier."""
    venster = await _venster(client, "elektriciteit", KWARTIER, kwartiergrens)
    if venster is None:
        return
    aansluiting, vanaf, tot = venster
    per_entiteit = await _blokken(hass, set(telwerken.values()), vanaf, tot)
    blokken = {kanaal: per_entiteit[entiteit] for kanaal, entiteit in telwerken.items()}
    # Enkel kwartieren waarvan het laatste blok al gecompileerd is.
    laatste = max((t for b in blokken.values() for t in b), default=None)
    if laatste is None:
        return
    tot = min(tot, kwartiergrens(laatste + BLOK))
    rijen = kwartierrijen(blokken, vanaf=vanaf, tot=tot, tarief_1=conf["tarief_1"], enkelvoudig=conf["enkelvoudig"])
    for stuk in in_stukken(rijen):
        uitslag = await client.vraag("POST", f"/mij/aansluitingen/{aansluiting['id']}/meetwaarden",
                                     json={"meter": conf["meter"], "metingen": stuk})
        _LOGGER.debug("SEF P1-upload: %s", {k: uitslag.get(k) for k in ("status", "rijen", "nieuw", "fluvius_behouden")})
