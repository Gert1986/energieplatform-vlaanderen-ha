"""
sensor.py
~~~~~~~~~

Prijssensoren uit SEF `/mij/prijs`: all-in EUR/kWh incl. btw voor afname en
injectie, nu; bij een dynamisch contract de prijs per kwartier als attribuut.
Met `gas` in de YAML ook `/mij/gasprijs`: EUR/m³ incl. btw (ADR-0064).

:copyright: (c) 2026 Gert Botte
:license: Alle rechten voorbehouden
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from . import DOMAIN, SefFout

_LOGGER = logging.getLogger(__name__)
# Een prijs wijzigt hoogstens per kwartier (dynamisch) of per dag/nachtwissel.
INTERVAL = timedelta(minutes=15)
# Een gasprijs wijzigt per maand (index); elk uur volstaat.
GAS_INTERVAL = timedelta(hours=1)


async def async_setup_platform(hass: HomeAssistant, config: ConfigType, async_add_entities: AddEntitiesCallback,
                               discovery_info: DiscoveryInfoType | None = None) -> None:
    """Twee sensoren op één gedeelde opvraging; met `gas` in de YAML een derde
    op een eigen opvraging (een fout aan de gaskant raakt de andere niet)."""
    client = hass.data[DOMAIN]["client"]

    def haler(pad: str):
        async def haal() -> dict:
            try:
                return await client.vraag("GET", pad)
            except (SefFout, aiohttp.ClientError, TimeoutError) as fout:
                raise UpdateFailed(str(fout)) from fout
        return haal

    coordinator = DataUpdateCoordinator(hass, _LOGGER, name="SEF prijs", update_method=haler("/mij/prijs"),
                                        update_interval=INTERVAL)
    await coordinator.async_refresh()
    sensoren = [SefPrijs(coordinator, "afname"), SefPrijs(coordinator, "injectie")]
    if (discovery_info or {}).get("gas"):
        gas = DataUpdateCoordinator(hass, _LOGGER, name="SEF gasprijs", update_method=haler("/mij/gasprijs"),
                                    update_interval=GAS_INTERVAL)
        await gas.async_refresh()
        sensoren.append(SefGasprijs(gas))
    async_add_entities(sensoren)


class SefPrijs(CoordinatorEntity, SensorEntity):
    """`afname_nu` of `injectie_nu`; injectie is wat je krijgt."""

    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    # Tot 192 kwartieren: te groot voor de recorder (16 kB), en elk kwartier opnieuw.
    _unrecorded_attributes = frozenset({"kwartieren"})

    def __init__(self, coordinator: DataUpdateCoordinator, richting: str) -> None:
        super().__init__(coordinator)
        self._richting = richting
        self._attr_name = f"SEF {richting}prijs"
        self._attr_unique_id = f"sef_{richting}prijs"

    @property
    def native_value(self) -> float | None:
        waarde = (self.coordinator.data or {}).get(f"{self._richting}_nu")
        return None if waarde is None else float(waarde)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        kwartieren = data.get("kwartieren")
        return {
            "contract": f"{data.get('leverancier')} {data.get('product')}", "contracttype": data.get("contracttype"),
            "zone": data.get("zone_nu"), "waarschuwingen": data.get("waarschuwingen", []),
            "kwartieren": None if kwartieren is None else [
                {"start": k["tijdstip"], "prijs": None if k[self._richting] is None else float(k[self._richting])}
                for k in kwartieren],
        }


class SefGasprijs(CoordinatorEntity, SensorEntity):
    """Wat één m³ gas méér kost met je huidige gascontract, deze maand: de
    eenheid van een P1-gasmeter, dus rechtstreeks bruikbaar in het
    Energy-dashboard. Zonder omrekenfactor in SEF: onbekend."""

    _attr_name = "SEF gasprijs"
    _attr_unique_id = "sef_gasprijs"
    _attr_native_unit_of_measurement = "EUR/m³"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    @property
    def native_value(self) -> float | None:
        waarde = (self.coordinator.data or {}).get("eur_per_m3")
        return None if waarde is None else float(waarde)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "contract": f"{data.get('leverancier')} {data.get('product')}", "contracttype": data.get("contracttype"),
            "maand": data.get("maand"),
            "eur_per_kwh": None if data.get("eur_per_kwh") is None else float(data["eur_per_kwh"]),
            "kwh_per_m3": None if data.get("gas_kwh_per_m3") is None else float(data["gas_kwh_per_m3"]),
            "kwh_per_m3_maand": data.get("gas_kwh_per_m3_maand"), "waarschuwingen": data.get("waarschuwingen", []),
        }
