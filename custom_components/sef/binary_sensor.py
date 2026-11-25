"""
binary_sensor.py
~~~~~~~~~~~~~~~~

Sinds 0.18.0: `binary_sensor.sef_goedkoop_nu` (prijsrang hoogstens 25) en `binary_sensor.sef_duur_nu`
(minstens 75), lokaal uit de kwartieren van `/mij/prijs` (dezelfde opvraging als de prijssensoren). Onbekend
als de rang onbekend is. De drempels zijn vast; een optie volgt pas als iemand erom vraagt.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import DOMAIN
from .prijs import rang
from .sensor import _Kwartierklok


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Na het sensorplatform (`__init__.py`), dus de coördinator `SEF prijs` bestaat al."""
    prijs = hass.data[DOMAIN][entry.entry_id]["coordinators"]["SEF prijs"]
    async_add_entities([SefPrijsSignaal(prijs, "goedkoop", lambda r: r <= 25),
                        SefPrijsSignaal(prijs, "duur", lambda r: r >= 75)])


class SefPrijsSignaal(_Kwartierklok, CoordinatorEntity, BinarySensorEntity):
    """Aan als de prijsrang van nu binnen de drempel valt; springt op de kwartiergrens."""

    def __init__(self, coordinator: DataUpdateCoordinator, naam: str, test) -> None:
        super().__init__(coordinator)
        self._test = test
        self._attr_name = f"SEF {naam} nu"
        self._attr_unique_id = f"sef_{naam}_nu"

    @property
    def is_on(self) -> bool | None:
        r = rang((self.coordinator.data or {}).get("kwartieren") or [], dt_util.now())
        return None if r is None else self._test(r)
