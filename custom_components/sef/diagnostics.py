"""
diagnostics.py
~~~~~~~~~~~~~~

Diagnosebestand (*Apparaten en diensten → Energieplatform Vlaanderen → ⋮ → Diagnostiek downloaden*) om bij
een probleem mee te sturen. Belandt vaak in een publiek issue: geen sleutel, geen meter-ID, geen bedragen of
contract, geen eigen adres van SEF (een tailnet-naam kan naar de persoon leiden) en geen entity-ID's van de
telwerken (die kunnen een naam bevatten). Wel de instellingen, de versies, per opvraging of ze lukte en welke
velden SEF terugstuurde, en per upload wanneer hij laatst liep en lukte, met de foutcode.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSIE
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import DOMAIN, STANDAARD_URL, SefFout
from .p1 import KANALEN

GEHEIM = {"sleutel", "meter", *KANALEN, "gas", "batterij_laden", "batterij_ontladen"}


def samenvatting(data: dict | None) -> dict:
    """Welke velden er zijn, met enkel het aantal kwartieren en de waarschuwingen: geen bedragen."""
    data = data or {}
    return {"velden": sorted(data), "kwartieren": len(data.get("kwartieren") or []),
            "waarschuwingen": data.get("waarschuwingen", [])}


def foutcode(fout: BaseException | None) -> str | None:
    """Status en code van SEF, of enkel het soort fout: de tekst van een netwerkfout bevat het adres."""
    fout = None if fout is None else fout.__cause__ or fout
    if fout is None:
        return None
    return f"{fout.status} {fout.code}" if isinstance(fout, SefFout) else type(fout).__name__


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Instellingen (zonder geheimen), versies en de status van elke opvraging en upload bij SEF."""
    gegevens = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    integratie = await async_get_integration(hass, DOMAIN)
    conf = dict(gegevens.get("conf") or entry.data)
    return {
        "versie": str(integratie.version),
        "ha_versie": HA_VERSIE,
        "instellingen": async_redact_data(conf, GEHEIM if conf.get("url") == STANDAARD_URL else GEHEIM | {"url"}),
        "uploads": gegevens.get("uploads") or {},
        "opvragingen": {
            naam: {"gelukt": c.last_update_success,
                   "fout": foutcode(c.last_exception),
                   "interval_s": c.update_interval.total_seconds() if c.update_interval else None,
                   **samenvatting(c.data)}
            for naam, c in (gegevens.get("coordinators") or {}).items()
        },
    }
