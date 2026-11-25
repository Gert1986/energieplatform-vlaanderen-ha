"""
select.py
~~~~~~~~~

Sinds 0.20.0: `select.sef_modus` van de batterijplanner (ADR-0001). `monitor` (standaard): niets plannen.
`proefdraai`: plannen en in `sensor.sef_beslissing` tonen wat hij zou doen, zonder te sturen. Automatisch
sturen komt later, enkel na een proefdraai. De keuze blijft na een herstart.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN
from .sensor import Batterijplanner

MODI = ["monitor", "proefdraai"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Enkel met een batterij voor de planner (na het sensorplatform, dat de planner maakt)."""
    if planner := hass.data[DOMAIN][entry.entry_id].get("planner"):
        async_add_entities([SefModus(planner)])


class SefModus(SelectEntity, RestoreEntity):
    """De modus van de batterijplanner."""

    _attr_name = "SEF modus"
    _attr_unique_id = "sef_modus"
    _attr_options = MODI

    def __init__(self, planner: Batterijplanner) -> None:
        self._planner = planner

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        vorige = await self.async_get_last_state()
        if vorige is not None and vorige.state in MODI:
            self._planner.modus = vorige.state
            await self._planner.beslis()

    @property
    def current_option(self) -> str:
        return self._planner.modus

    async def async_select_option(self, option: str) -> None:
        self._planner.modus = option
        self.async_write_ha_state()
        await self._planner.beslis()
