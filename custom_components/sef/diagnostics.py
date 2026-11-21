"""
diagnostics.py
~~~~~~~~~~~~~~

Diagnosebestand (*Apparaten en diensten → Energieplatform Vlaanderen → ⋮ → Diagnostiek downloaden*) om bij
een probleem mee te sturen. Belandt vaak in een publiek issue: geen sleutel, geen meter-ID, geen bedragen of
contract, enkel de instellingen en per opvraging of ze lukte en welke velden SEF terugstuurde.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import DOMAIN

GEHEIM = {"sleutel", "meter"}


def samenvatting(data: dict | None) -> dict:
    """Welke velden er zijn, met enkel het aantal kwartieren en de waarschuwingen: geen bedragen."""
    data = data or {}
    return {"velden": sorted(data), "kwartieren": len(data.get("kwartieren") or []),
            "waarschuwingen": data.get("waarschuwingen", [])}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Instellingen (zonder geheimen), versie en de status van elke opvraging bij SEF."""
    gegevens = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    integratie = await async_get_integration(hass, DOMAIN)
    return {
        "versie": str(integratie.version),
        "instellingen": async_redact_data(dict(gegevens.get("conf") or entry.data), GEHEIM),
        "opvragingen": {
            naam: {"gelukt": c.last_update_success,
                   "fout": None if c.last_exception is None else str(c.last_exception),
                   "interval_s": c.update_interval.total_seconds() if c.update_interval else None,
                   **samenvatting(c.data)}
            for naam, c in (gegevens.get("coordinators") or {}).items()
        },
    }
