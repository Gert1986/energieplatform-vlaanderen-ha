"""
sensor.py
~~~~~~~~~

Prijssensoren uit SEF `/mij/prijs`: all-in EUR/kWh incl. btw voor afname en
injectie, nu, met de prijs per kwartier van vandaag en morgen als attribuut.
Met `gas` ook `/mij/gasprijs`: EUR/m³ incl. btw (ADR-0064). Waakhond uit
`/mij/waakhond`: wat het goedkoopste contract uit je laatste vergelijking
minder kost dan je huidige, en het aantal dagen tot je contract eindigt.
Voorschot uit `/mij/voorschot` (ADR-0078): de kost van deze maand en de
jaarinschatting tegenover je maandvoorschot, en het saldo op de afrekening.

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
# De kosten volgen de meting (P1 elk kwartier, maar SEF rekent per volledige dag); elk uur volstaat.
VOORSCHOT_INTERVAL = timedelta(hours=1)


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
    sensoren: list[SensorEntity] = [SefPrijs(prijs, "afname"), SefPrijs(prijs, "injectie"),
                                    SefContract(prijs, "elektriciteit")]
    if "gas" in conf:
        gasprijs = await coordinator("SEF gasprijs", "/mij/gasprijs", GAS_INTERVAL)
        sensoren += [SefGasprijs(gasprijs), SefContract(gasprijs, "gas")]
    waakhond = await coordinator("SEF waakhond", "/mij/waakhond", WAAKHOND_INTERVAL)
    gas_in_sef = any(((waakhond.data or {}).get("gas") or {}).get(k) for k in ("contract", "vergelijking"))
    for energie in ("elektriciteit", "gas") if "gas" in conf or gas_in_sef else ("elektriciteit",):
        sensoren += [SefBesparing(waakhond, energie), SefContractEinde(waakhond, energie)]
    voorschot = await coordinator("SEF voorschot", "/mij/voorschot", VOORSCHOT_INTERVAL)
    gas_voorschot = any(a.get("energie") == "gas" for a in (voorschot.data or {}).get("aansluitingen", []))
    for energie in ("elektriciteit", "gas") if "gas" in conf or gas_voorschot else ("elektriciteit",):
        sensoren += [SefKostMaand(voorschot, energie), SefJaarschatting(voorschot, energie),
                     SefVoorschotSaldo(voorschot, energie)]
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


class SefContract(CoordinatorEntity, SensorEntity):
    """Je huidige contract zoals SEF ermee rekent: leverancier en product, met
    een link naar de tariefkaart die de prijs bepaalt (null zonder gekende
    kaart, bv. een prijs uit het VREG-werkboek). Uit dezelfde opvraging als de
    prijssensor, geen extra verzoek. Zonder contract in SEF: onbeschikbaar."""

    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator)
        self._attr_name = f"SEF contract {energie}"
        self._attr_unique_id = f"sef_contract_{energie}"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        return " ".join(filter(None, (data.get("leverancier"), data.get("product")))) or None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {k: data.get(k) for k in ("leverancier", "product", "contracttype", "vreg_id", "prijsbron",
                                         "contractdatum", "tariefkaart_url")}


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


def _getal(waarde: str | None) -> float | None:
    return None if waarde is None else float(waarde)


class _SefVoorschot(CoordinatorEntity, SensorEntity):
    """Basis voor de voorschotsensoren: de eerste aansluiting van `energie` in
    `/mij/voorschot`. Bedragen in EUR incl. btw; onbekend met `reden`."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str, naam: str, sleutel: str) -> None:
        super().__init__(coordinator)
        self._energie = energie
        self._attr_name = f"SEF {naam} {energie}"
        self._attr_unique_id = f"sef_{sleutel}_{energie}"

    def _a(self) -> dict:
        return next((a for a in (self.coordinator.data or {}).get("aansluitingen", [])
                     if a.get("energie") == self._energie), {})

    def _deel(self, naam: str) -> dict:
        return self._a().get(naam) or {}

    def _reden(self, naam: str) -> str | None:
        return self._a().get("reden") or self._deel(naam).get("reden")


class SefKostMaand(_SefVoorschot):
    """Wat je verbruik deze maand al kostte, tot de laatste volledige meetdag,
    met het voorschot voor diezelfde dagen (naar rato) en het verschil
    (positief = meer dan je voorschot)."""

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator, energie, "kost deze maand", "kost_maand")

    @property
    def native_value(self) -> float | None:
        return _getal(self._deel("maand").get("kost_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        m, v = self._deel("maand"), self._deel("vorige_maand")
        return {
            "voorschot_maand": _getal(self._a().get("voorschot_eur_maand")),
            "voorschot_tot_nu": _getal(m.get("voorschot_eur")), "verschil": _getal(m.get("verschil_eur")),
            "van": m.get("van"), "tot": m.get("tot"),
            "vorige_maand_kost": _getal(v.get("kost_eur")), "vorige_maand_verschil": _getal(v.get("verschil_eur")),
            "contract": self._a().get("contract"), "reden": self._reden("maand"),
            "waarschuwingen": m.get("waarschuwingen", []),
        }


class SefJaarschatting(_SefVoorschot):
    """Wat je verbruik van de laatste 12 volle maanden kost met je huidige
    contract, met 12 × je voorschot en het aangeraden maandvoorschot."""

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator, energie, "jaarschatting", "jaarschatting")

    @property
    def native_value(self) -> float | None:
        return _getal(self._deel("jaar").get("kost_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        j = self._deel("jaar")
        return {
            "voorschot_jaar": _getal(j.get("voorschot_eur")), "saldo": _getal(j.get("saldo_eur")),
            "aangeraden_voorschot": _getal(j.get("aangeraden_voorschot_eur")),
            "van": j.get("van"), "tot": j.get("tot"), "contract": self._a().get("contract"),
            "reden": self._reden("jaar"), "waarschuwingen": j.get("waarschuwingen", []),
        }


class SefVoorschotSaldo(_SefVoorschot):
    """Jaarinschatting min 12 × je voorschot: positief = bijbetalen op de
    afrekening, negatief = terugkrijgen. Onbekend zonder voorschot in SEF."""

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator, energie, "voorschot saldo", "voorschot_saldo")

    @property
    def native_value(self) -> float | None:
        return _getal(self._deel("jaar").get("saldo_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        j = self._deel("jaar")
        reden = self._reden("jaar")
        if reden is None and self._a() and self._a().get("voorschot_eur_maand") is None:
            reden = "Geef je maandvoorschot in op de website (Mijn voorschot)."
        return {"voorschot_maand": _getal(self._a().get("voorschot_eur_maand")),
                "aangeraden_voorschot": _getal(j.get("aangeraden_voorschot_eur")), "reden": reden}
