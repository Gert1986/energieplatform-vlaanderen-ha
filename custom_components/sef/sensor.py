"""
sensor.py
~~~~~~~~~

Prijssensoren uit SEF `/mij/prijs`: all-in EUR/kWh incl. btw voor afname en
injectie, nu, met de prijs per kwartier van vandaag en morgen als attribuut.
Met `gas` ook `/mij/gasprijs`: EUR/m³ incl. btw (ADR-0064). Waakhond uit
`/mij/waakhond`: wat het goedkoopste contract uit je laatste vergelijking
minder kost dan je huidige, en het aantal dagen tot je contract eindigt.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from . import DOMAIN, SefFout

_LOGGER = logging.getLogger(__name__)
# Een prijs wijzigt hoogstens per kwartier (dynamisch) of per dag/nachtwissel.
INTERVAL = timedelta(minutes=15)
# Een gasprijs wijzigt per maand (index); elk uur volstaat.
GAS_INTERVAL = timedelta(hours=1)
# De waakhond leest enkel bewaarde vergelijkingen; die wijzigen als je er een start.
WAAKHOND_INTERVAL = timedelta(hours=1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Elke opvraging een eigen coördinator: een fout aan de ene kant raakt de andere niet."""
    gegevens = hass.data[DOMAIN][entry.entry_id]
    client, conf = gegevens["client"], gegevens["conf"]

    async def coordinator(naam: str, pad: str, interval: timedelta) -> DataUpdateCoordinator:
        async def haal() -> dict:
            try:
                return await client.vraag("GET", pad)
            except (SefFout, aiohttp.ClientError, TimeoutError) as fout:
                raise UpdateFailed(str(fout)) from fout
        c = DataUpdateCoordinator(hass, _LOGGER, name=naam, update_method=haal, update_interval=interval)
        await c.async_refresh()  # SEF onbereikbaar: sensoren onbeschikbaar, geen geblokkeerde opstart
        return c

    prijs = await coordinator("SEF prijs", "/mij/prijs", INTERVAL)
    sensoren: list[SensorEntity] = [SefPrijs(prijs, "afname"), SefPrijs(prijs, "injectie")]
    if "gas" in conf:
        sensoren.append(SefGasprijs(await coordinator("SEF gasprijs", "/mij/gasprijs", GAS_INTERVAL)))
    waakhond = await coordinator("SEF waakhond", "/mij/waakhond", WAAKHOND_INTERVAL)
    gas_in_sef = any(((waakhond.data or {}).get("gas") or {}).get(k) for k in ("contract", "vergelijking"))
    for energie in ("elektriciteit", "gas") if "gas" in conf or gas_in_sef else ("elektriciteit",):
        sensoren += [SefBesparing(waakhond, energie), SefContractEinde(waakhond, energie)]
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
        return {
            "contract": f"{data.get('leverancier')} {data.get('product')}", "contracttype": data.get("contracttype"),
            "zone": data.get("zone_nu"), "waarschuwingen": data.get("waarschuwingen", []),
            "kwartieren": [{"start": k["tijdstip"], "prijs": None if k[self._richting] is None else float(k[self._richting])}
                           for k in data.get("kwartieren") or []],
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


def _naam(contract: dict | None) -> str | None:
    return None if not contract else f"{contract.get('leverancier')} {contract.get('product')}"


class SefBesparing(CoordinatorEntity, SensorEntity):
    """Wat het goedkoopste contract uit je laatste vergelijking minder kost dan
    je huidige, over de periode van die vergelijking (negatief = je huidige is
    goedkoper). Onbekend zonder vergelijking of doorgerekend huidig contract;
    `reden` zegt waarom."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator)
        self._energie = energie
        self._attr_name = f"SEF besparing {energie}"
        self._attr_unique_id = f"sef_besparing_{energie}"

    def _data(self) -> dict:
        return (self.coordinator.data or {}).get(self._energie) or {}

    @property
    def native_value(self) -> float | None:
        waarde = self._data().get("besparing_eur")
        return None if waarde is None else float(waarde)

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data()
        vergelijking, beste = d.get("vergelijking") or {}, d.get("beste") or {}
        return {
            "beste_contract": _naam(beste or None), "beste_contracttype": beste.get("contracttype"),
            "huidig_contract": _naam(d.get("huidig")),
            "periode_van": vergelijking.get("periode_van"), "periode_tot": vergelijking.get("periode_tot"),
            "vergeleken_op": vergelijking.get("afgerond_op"), "reden": d.get("reden"),
        }


class SefContractEinde(CoordinatorEntity, SensorEntity):
    """Dagen tot je lopende contractperiode (Mijn contracten) eindigt;
    onbekend bij een contract zonder einddatum."""

    _attr_native_unit_of_measurement = "d"
    _attr_device_class = SensorDeviceClass.DURATION

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator)
        self._energie = energie
        self._attr_name = f"SEF contract {energie} dagen tot einde"
        self._attr_unique_id = f"sef_contract_einde_{energie}"

    def _contract(self) -> dict:
        return ((self.coordinator.data or {}).get(self._energie) or {}).get("contract") or {}

    @property
    def native_value(self) -> int | None:
        return self._contract().get("dagen_tot_einde")

    @property
    def extra_state_attributes(self) -> dict:
        c = self._contract()
        return {"contract": _naam(c or None), "van": c.get("van"), "tot": c.get("tot")}
