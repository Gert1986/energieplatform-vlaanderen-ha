"""
sensor.py
~~~~~~~~~

Prijssensoren uit SEF `/mij/prijs`: all-in EUR/kWh incl. btw voor afname en
injectie, nu, met de prijs per kwartier van vandaag en morgen als attribuut.
Met `gas` ook `/mij/gasprijs`: EUR/m³ incl. btw (ADR-0064). Waakhond uit
`/mij/waakhond`: wat het goedkoopste contract uit je laatste vergelijking
minder kost dan je huidige, het aantal dagen tot je contract eindigt en
(sinds 0.11.0) de gemiddelde all-in prijs per kWh van je huidige contract.
Voorschot uit `/mij/voorschot` (ADR-0078): de kost van deze maand en de
jaarinschatting tegenover je maandvoorschot, en het saldo op de afrekening;
sinds 0.9.0 ook de kost sinds je laatste afrekening (ADR-0085). Sinds 0.12.0 de day-ahead-
prijs van de beurs (EUR/MWh) per kwartier, uit dezelfde opvraging als de prijssensoren.
Sinds 0.13.0, ook uit die opvraging: de beursprijs all-in (EUR/kWh, met dezelfde netkosten,
heffingen en btw als je contract) en de maandpieken met hun kost aan capaciteitstarief.
Sinds 0.14.0 dragen de prijssensoren het attribuut `opbouw`: afname in energie, net, heffingen en btw; injectie in beurs en marge.
Sinds 0.14.1 volgen ze het kwartier van de klok (`prijs.prijs_nu`); sinds 0.15.1 schrijven ze hun toestand
ook op elke kwartiergrens weg (`_Kwartierklok`), anders zag HA de nieuwe prijs pas bij de volgende opvraging.
Sinds 0.18.0 de prijssignalen, lokaal uit dezelfde kwartieren: de rang van het kwartier van nu en het begin
van het goedkoopste blok van 1, 2 en 4 uur (de aan/uit-signalen staan in `binary_sensor.py`).
Sinds 0.19.0 Peak Guard (`Piekwacht`, rekenwerk in `piek.py`): het vermogen van het lopende kwartier uit de
afnametelwerken, de verwachte kwartierpiek, de ruimte tot de drempel en wat een nieuwe piek kost. Elke minuut,
lokaal; enkel de drempel en het tarief komen uit de laatst gelukte opvraging bij SEF.
Sinds 0.20.0 de batterijplanner (`Batterijplanner`, beslissing in `planner.py`, ADR-0001): met `select.sef_modus`
op proefdraai zegt `sensor.sef_beslissing` wat hij zou doen. Er wordt niets gestuurd.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import DOMAIN, SefFout
from .p1 import kwartier_nu
from .planner import beslis
from .piek import KWARTIER as PIEK_KWARTIER, drempel, kost_nieuwe_piek, kwartierstand, verwachte_piek, \
    vermogen_uit_telwerk
from .prijs import KWARTIER, getal as _getal, goedkoopste_venster, horizon, opbouw_attribuut, prijs_nu, rang

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
        gegevens.setdefault("coordinators", {})[naam] = c  # voor diagnostics.py
        return c

    prijs = await coordinator("SEF prijs", "/mij/prijs", INTERVAL)
    sensoren: list[SensorEntity] = [SefPrijs(prijs, "afname"), SefPrijs(prijs, "injectie"),
                                    SefContract(prijs, "elektriciteit"), SefSpotprijs(prijs), SefBeursAllin(prijs),
                                    SefMaandpiek(prijs), SefHoogstePiek(prijs), SefCapaciteitstarief(prijs),
                                    SefPrijsrang(prijs), *(SefGoedkoopste(prijs, uren) for uren in (1, 2, 4))]
    if "gas" in conf:
        gasprijs = await coordinator("SEF gasprijs", "/mij/gasprijs", GAS_INTERVAL)
        sensoren += [SefGasprijs(gasprijs), SefContract(gasprijs, "gas")]
    waakhond = await coordinator("SEF waakhond", "/mij/waakhond", WAAKHOND_INTERVAL)
    gas_in_sef = any(((waakhond.data or {}).get("gas") or {}).get(k) for k in ("contract", "vergelijking"))
    for energie in ("elektriciteit", "gas") if "gas" in conf or gas_in_sef else ("elektriciteit",):
        sensoren += [SefBesparing(waakhond, energie), SefContractEinde(waakhond, energie),
                     SefGemiddeldePrijs(waakhond, energie)]
    voorschot = await coordinator("SEF voorschot", "/mij/voorschot", VOORSCHOT_INTERVAL)
    gas_voorschot = any(a.get("energie") == "gas" for a in (voorschot.data or {}).get("aansluitingen", []))
    for energie in ("elektriciteit", "gas") if "gas" in conf or gas_voorschot else ("elektriciteit",):
        sensoren += [SefKostMaand(voorschot, energie), SefJaarschatting(voorschot, energie),
                     SefVoorschotSaldo(voorschot, energie), SefKostSindsAfrekening(voorschot, energie)]
    afname = [conf[k] for k in ("afname_1", "afname_2") if k in conf]
    if afname:
        wacht = gegevens["piekwacht"] = Piekwacht(hass, afname, conf.get("vermogen"), prijs)
        wacht.start(entry)
        sensoren += [SefPiekwachtSensor(wacht, *s) for s in PIEKSENSOREN]
    if "batterij_soc" in conf:
        planner = gegevens["planner"] = Batterijplanner(hass, conf, prijs, gegevens.get("piekwacht"))
        planner.start(entry)
        sensoren.append(SefBeslissing(planner))
    async_add_entities(sensoren)


def _waarde(state: State | None) -> Decimal | None:
    """Een telwerk (kWh) of vermogen (kW) als Decimal; Wh en W worden kWh en kW. None als de entiteit
    ontbreekt, onbekend of onbeschikbaar is."""
    try:
        w = Decimal(state.state)
    except (AttributeError, InvalidOperation):
        return None
    if not w.is_finite():
        return None
    return w / 1000 if state.attributes.get("unit_of_measurement") in ("Wh", "W") else w


def _dec(waarde) -> Decimal | None:
    return None if waarde is None else Decimal(str(waarde))


class Piekwacht:
    """Houdt de telwerkstand bij het begin van het kwartier en van de vorige minuut bij, en de hoogste
    kwartierpiek die deze maand lokaal gezien is; rekent elke minuut de cijfers voor de piekentiteiten.
    `cijfers` is None als een telwerk onbeschikbaar is: dan zijn alle piekentiteiten onbeschikbaar, nooit
    `piekrisico = aan` op onbetrouwbare data. Na een herstart midden in een kwartier is dat kwartier onbekend.
    # ponytail: eerste kwartier na herstart onbekend; begintoestand uit de recorder halen als dat stoort"""

    def __init__(self, hass: HomeAssistant, telwerken: list[str], vermogen: str | None,
                 prijs: DataUpdateCoordinator) -> None:
        self._hass, self._telwerken, self._vermogen, self._prijs = hass, telwerken, vermogen, prijs
        self._begin: tuple[datetime, Decimal | None] | None = None   # (kwartierbegin, stand of None)
        self._vorige: tuple[datetime, Decimal] | None = None
        self._lokaal: tuple[str, Decimal] | None = None               # (maand, hoogste kwartier-kW)
        self.cijfers: dict | None = None
        self.luisteraars: list[Callable[[], None]] = []

    def start(self, entry: ConfigEntry) -> None:
        entry.async_on_unload(async_track_time_change(self._hass, self._tik, second=0))
        self._tik(dt_util.now())

    def _stand(self) -> Decimal | None:
        standen = [_waarde(self._hass.states.get(e)) for e in self._telwerken]
        return None if None in standen else sum(standen, Decimal(0))

    @callback
    def _tik(self, nu: datetime) -> None:
        nu = dt_util.as_utc(nu)  # in UTC: twee tijden in dezelfde ZoneInfo trekt Python af in kloktijd (zomer/winter)
        stand = self._stand()
        kwartier = nu.replace(minute=nu.minute - nu.minute % 15, second=0, microsecond=0)
        if self._begin is None or self._begin[0] != kwartier:
            vorig = self._begin
            if vorig is not None and vorig[1] is not None and stand is not None and vorig[0] + PIEK_KWARTIER == kwartier:
                gemiddeld = kwartierstand(vorig[1], stand, vorig[0], kwartier)
                maand = f"{dt_util.as_local(vorig[0]):%Y-%m}"
                if gemiddeld is not None and (self._lokaal is None or self._lokaal[0] != maand
                                              or gemiddeld > self._lokaal[1]):
                    self._lokaal = (maand, gemiddeld)
            op_de_grens = nu - kwartier < timedelta(seconds=30)
            self._begin = (kwartier, stand if op_de_grens else None)
            self._vorige = (nu, stand) if stand is not None else None
            if op_de_grens and stand is not None and self.cijfers is not None:
                return  # de cijfers van het afgelopen kwartier blijven nog een minuut staan
        self.cijfers = None if stand is None else self._reken(nu, stand)
        self._vorige = None if stand is None else (nu, stand)
        for luisteraar in self.luisteraars:
            luisteraar()

    def _reken(self, nu: datetime, stand: Decimal) -> dict:
        begin, begin_stand = self._begin
        gemiddeld = kwartierstand(begin_stand, stand, begin, nu)
        vermogen = _waarde(self._hass.states.get(self._vermogen)) if self._vermogen else None
        if vermogen is None:  # geen of onbeschikbare vermogenssensor: uit het telwerk van de laatste minuut
            vermogen = vermogen_uit_telwerk(self._vorige, (nu, stand))
        verwacht = verwachte_piek(gemiddeld, vermogen, nu - begin)
        cap = (self._prijs.data or {}).get("capaciteit") or {}  # de laatst gelukte opvraging, ook als SEF nu faalt
        maand = f"{dt_util.as_local(nu):%Y-%m}"
        sef_piek = next((_dec(m.get("piek_kw")) for m in cap.get("maanden", []) if m.get("maand") == maand), None)
        lokaal = self._lokaal[1] if self._lokaal and self._lokaal[0] == maand else None
        grens, bron = drempel(sef_piek, lokaal, _dec(cap.get("ondergrens_kw")))
        risico = None if grens is None or verwacht is None else verwacht > grens
        return {"kwartiervermogen": gemiddeld, "verwacht": verwacht, "drempel": grens, "bron": bron,
                "ruimte": None if risico is None else grens - verwacht, "risico": risico,
                "kost": kost_nieuwe_piek(verwacht, grens, _dec(cap.get("tarief_eur_kw_jaar"))),
                "reden": None if risico is None else (
                    f"verwacht {verwacht:.2f} kW {'boven' if risico else 'onder'} de drempel {grens:.2f} kW ({bron})")}


class _Piekwachter:
    """Basis voor de piekentiteiten: geen CoordinatorEntity (die is onbeschikbaar zodra SEF faalt), maar
    een luisteraar op `Piekwacht`."""

    _attr_should_poll = False

    def __init__(self, wacht: Piekwacht) -> None:
        self._wacht = wacht

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._wacht.luisteraars.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self._wacht.luisteraars.remove(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self._wacht.cijfers is not None


# (naam, sleutel in Piekwacht.cijfers, eenheid, device_class)
PIEKSENSOREN = [("kwartiervermogen", "kwartiervermogen", "kW", SensorDeviceClass.POWER),
                ("verwachte kwartierpiek", "verwacht", "kW", SensorDeviceClass.POWER),
                ("piekruimte", "ruimte", "kW", SensorDeviceClass.POWER),
                ("kost nieuwe piek", "kost", "EUR", SensorDeviceClass.MONETARY)]


class SefPiekwachtSensor(_Piekwachter, SensorEntity):
    """Kwartiervermogen (gemiddelde tot nu), verwachte kwartierpiek, piekruimte (kW tot de drempel, negatief =
    erover) en de kost van de nieuwe piek (EUR incl. btw deze maand, 0 onder de drempel)."""

    _attr_suggested_display_precision = 2

    def __init__(self, wacht: Piekwacht, naam: str, sleutel: str, eenheid: str, klasse: SensorDeviceClass) -> None:
        super().__init__(wacht)
        self._sleutel = sleutel
        self._attr_name = f"SEF {naam}"
        self._attr_unique_id = "sef_" + naam.replace(" ", "_")
        self._attr_native_unit_of_measurement = eenheid
        self._attr_device_class = klasse
        if klasse is SensorDeviceClass.POWER:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        w = (self._wacht.cijfers or {}).get(self._sleutel)
        return None if w is None else round(float(w), 3)


class Batterijplanner:
    """Elke minuut (na Peak Guard, of op de kwartiergrens zonder) en bij elke moduswissel: wat de batterij nu
    zou doen. In `monitor` rekent hij niet. `beslissing` wisselt enkel van tekst als actie of reden wisselt,
    zodat het logboek beknopt blijft; dan ook het event `sef_beslissing`. Stuurt niets (proefdraai)."""

    def __init__(self, hass: HomeAssistant, conf: dict, prijs: DataUpdateCoordinator, wacht: Piekwacht | None) -> None:
        self._hass, self._conf, self._prijs, self._wacht = hass, conf, prijs, wacht
        self.modus = "monitor"
        self.beslissing: dict = {"tekst": "Monitor", "actie": None, "reden": "monitor", "vermogen_kw": Decimal(0),
                                 "detail": ""}
        self.luisteraars: list[Callable[[], None]] = []
        self._zon: tuple[date, Decimal] | None = None

    def start(self, entry: ConfigEntry) -> None:
        if self._wacht is not None:
            self._wacht.luisteraars.append(self._tik)
        else:
            entry.async_on_unload(async_track_time_change(self._hass, lambda _nu: self._tik(),
                                                          minute=[0, 15, 30, 45], second=0))

    @callback
    def _tik(self) -> None:
        if self.modus != "monitor":
            self._hass.async_create_task(self.beslis())

    async def _zon_kwh(self) -> Decimal:
        """Wat gisteren geïnjecteerd én in de batterij geladen werd (kWh): zoveel ruimte blijft vrij voor
        zonne-overschot. Met enkel de injectie zag een zonnige dag die net de batterij vulde eruit als 0.
        # ponytail: gisteren = morgen, en enkel juist zolang niets de batterij van het net laadt (eigen schema
        # van de batterij, later `automatisch`); een PV-voorspelling als die er komt"""
        vandaag = dt_util.now().date()
        if self._zon is not None and self._zon[0] == vandaag:
            return self._zon[1]
        telwerken = {self._conf[k] for k in ("injectie_1", "injectie_2", "batterij_laden") if k in self._conf}
        kwh = Decimal(0)
        if telwerken:
            begin = dt_util.start_of_local_day() - timedelta(days=1)
            ruw = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period, self._hass, begin, begin + timedelta(days=1), telwerken, "hour",
                {"energy": "kWh"}, {"change"})
            kwh = sum((Decimal(str(r["change"])) for rijen in ruw.values() for r in rijen if r.get("change")),
                      Decimal(0))
        self._zon = (vandaag, kwh)
        return kwh

    async def beslis(self) -> None:
        if self.modus == "monitor":
            nieuw = {"tekst": "Monitor", "actie": None, "reden": "monitor", "vermogen_kw": Decimal(0), "detail": ""}
        else:
            c = self._conf
            nieuw = beslis((self._prijs.data or {}).get("kwartieren") or [], dt_util.now(),
                           soc_pct=_waarde(self._hass.states.get(c["batterij_soc"])),
                           capaciteit_kwh=_dec(c["batterij_capaciteit"]),
                           laadvermogen_kw=_dec(c["batterij_vermogen"]) / 1000,
                           max_soc_pct=_dec(c.get("batterij_max_soc", 100)),
                           piekruimte_kw=(self._wacht.cijfers or {}).get("ruimte") if self._wacht else None,
                           zon_kwh=await self._zon_kwh())
            nieuw["tekst"] = ("Zou laden" if nieuw["actie"] == "laden" else "Zelfverbruik") + f": {nieuw['reden']}"
        if nieuw["tekst"] != self.beslissing["tekst"]:
            self._hass.bus.async_fire("sef_beslissing", {"modus": self.modus, "actie": nieuw["actie"],
                                                         "reden": nieuw["reden"], "detail": nieuw["detail"]})
        self.beslissing = nieuw
        for luisteraar in self.luisteraars:
            luisteraar()


class SefBeslissing(SensorEntity):
    """Wat de batterijplanner nu zou doen, als korte tekst. Geen eenheid of state_class: zo toont het logboek
    elke wissel met het tijdstip (een logboekkaart op het dashboard). Prijzen en vermogen in de attributen."""

    _attr_name = "SEF beslissing"
    _attr_unique_id = "sef_beslissing"
    _attr_should_poll = False

    def __init__(self, planner: Batterijplanner) -> None:
        self._planner = planner

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._planner.luisteraars.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self._planner.luisteraars.remove(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._planner.beslissing["tekst"]

    @property
    def extra_state_attributes(self) -> dict:
        b = self._planner.beslissing
        return {"modus": self._planner.modus, "actie": b["actie"], "vermogen_w": int(b["vermogen_kw"] * 1000),
                "detail": b["detail"]}


class _Kwartierklok:
    """Schrijft de toestand opnieuw weg op elke kwartiergrens. `native_value` kiest het kwartier van de
    klok, maar HA leest die pas als de coördinator ververst (om de 15 min, gerekend vanaf het opstarten):
    zonder deze tik verscheen de nieuwe prijs tot 15 minuten te laat. Vóór CoordinatorEntity in de bases."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_change(self.hass, self._kwartier_tik, minute=[0, 15, 30, 45], second=0))

    @callback
    def _kwartier_tik(self, _nu) -> None:
        self.async_write_ha_state()


class SefPrijs(_Kwartierklok, CoordinatorEntity, SensorEntity):
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
        return prijs_nu(self.coordinator.data or {}, self._richting, dt_util.now(), kwartier_nu)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "contract": f"{data.get('leverancier')} {data.get('product')}", "contracttype": data.get("contracttype"),
            "zone": data.get("zone_nu"), "waarschuwingen": data.get("waarschuwingen", []),
            "kwartieren": [{"start": k["tijdstip"], "prijs": None if k[self._richting] is None else float(k[self._richting])}
                           for k in data.get("kwartieren") or []],
            # De splitsing van de prijs van nu, EUR/kWh: afname = energie, net, heffingen, btw; injectie = beurs, marge.
            **opbouw_attribuut(data, self._richting),
        }


class SefSpotprijs(_Kwartierklok, CoordinatorEntity, SensorEntity):
    """De day-ahead-prijs van de Belgische beurs voor het kwartier van nu (EUR/MWh, zonder btw of
    kosten), bij elk contract: bij een vast of variabel contract ter informatie. Bron: ENTSO-E, met
    energy-charts.info als terugval. Morgen pas na de publicatie rond 13 uur."""

    _attr_name = "SEF spotprijs"
    _attr_unique_id = "sef_spotprijs"
    _attr_native_unit_of_measurement = "EUR/MWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"kwartieren"})

    @property
    def native_value(self) -> float | None:
        k = kwartier_nu((self.coordinator.data or {}).get("kwartieren") or [], dt_util.now())
        return None if k is None or k.get("spot_eur_mwh") is None else float(k["spot_eur_mwh"])

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "waarschuwingen": [w for w in data.get("waarschuwingen", []) if w.startswith("Groothandelsprijs")],
            "kwartieren": [{"start": k["tijdstip"], "prijs": float(k["spot_eur_mwh"])}
                           for k in data.get("kwartieren") or [] if k.get("spot_eur_mwh") is not None],
        }


class SefBeursAllin(_Kwartierklok, CoordinatorEntity, SensorEntity):
    """De beursprijs van het kwartier van nu, ingepakt zoals je eigen prijs: dezelfde netkosten,
    heffingen en btw (EUR/kWh). Zo staat ze op dezelfde voet als `sensor.sef_afnameprijs`; het
    verschil is wat je leverancier bovenop de beurs rekent (marge, groene stroom, WKK). Vaste kosten
    zitten in geen van beide. Morgen pas na de publicatie rond 13 uur."""

    _attr_name = "SEF beursprijs all-in"
    _attr_unique_id = "sef_beursprijs_allin"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _unrecorded_attributes = frozenset({"kwartieren"})

    @property
    def native_value(self) -> float | None:
        k = kwartier_nu((self.coordinator.data or {}).get("kwartieren") or [], dt_util.now())
        return None if k is None else _getal(k.get("beurs_allin"))

    @property
    def extra_state_attributes(self) -> dict:
        return {"kwartieren": [{"start": k["tijdstip"], "prijs": float(k["beurs_allin"])}
                               for k in (self.coordinator.data or {}).get("kwartieren") or []
                               if k.get("beurs_allin") is not None]}


class SefPrijsrang(_Kwartierklok, CoordinatorEntity, SensorEntity):
    """0 tot 100: hoeveel procent van de gekende kwartieren vanaf nu strikt goedkoper is dan dit kwartier.
    0 = nu is het goedkoopst. Bij dag/nacht zijn alle nachtkwartieren 0; `prijsniveaus` toont dan 2."""

    _attr_name = "SEF prijsrang"
    _attr_unique_id = "sef_prijsrang"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return rang((self.coordinator.data or {}).get("kwartieren") or [], dt_util.now())

    @property
    def extra_state_attributes(self) -> dict:
        h = horizon((self.coordinator.data or {}).get("kwartieren") or [], dt_util.now())
        return {"horizon_tot": (h[-1][0] + KWARTIER).isoformat() if h else None,
                "prijsniveaus": len({prijs for _, prijs in h})}


class SefGoedkoopste(_Kwartierklok, CoordinatorEntity, SensorEntity):
    """Het begin van het goedkoopste aaneengesloten blok van 1, 2 of 4 uur in de gekende prijzen. Eens
    begonnen blijft het staan tot het voorbij is (geen verschuiving midden in je vaatwasser); onbekend als
    er minder prijzen gekend zijn dan het blok lang is."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: DataUpdateCoordinator, uren: int) -> None:
        super().__init__(coordinator)
        self._aantal = 4 * uren
        self._attr_name = f"SEF goedkoopste {uren}u"
        self._attr_unique_id = f"sef_goedkoopste_{uren}u"

    _lopend: dict | None = None

    def _venster(self) -> dict | None:
        # ponytail: na een herstart midden in een blok is het lopende blok vergeten en volgt het volgende
        nu = dt_util.now()
        if self._lopend is None or not self._lopend["begin"] <= nu < self._lopend["einde"]:
            self._lopend = goedkoopste_venster((self.coordinator.data or {}).get("kwartieren") or [], nu, self._aantal)
        return self._lopend

    @property
    def native_value(self):
        v = self._venster()
        return None if v is None else v["begin"]

    @property
    def extra_state_attributes(self) -> dict:
        v = self._venster()
        return {"einde": None if v is None else v["einde"].isoformat(),
                "gemiddelde_prijs": None if v is None else float(v["gemiddelde"])}


class _SefPiek(CoordinatorEntity, SensorEntity):
    """Basis voor de piek- en capaciteitssensoren: `capaciteit` uit `/mij/prijs` (null bij een
    analoge meter of een oudere SEF: onbekend). Kosten in EUR incl. btw op het tarief van nu."""

    def __init__(self, coordinator: DataUpdateCoordinator, naam: str, sleutel: str) -> None:
        super().__init__(coordinator)
        self._attr_name = f"SEF {naam}"
        self._attr_unique_id = f"sef_{sleutel}"

    def _c(self) -> dict:
        return (self.coordinator.data or {}).get("capaciteit") or {}

    def _maanden(self) -> list[dict]:
        return [{"maand": m["maand"], "piek_kw": float(m["piek_kw"]), "kost_eur": round(float(m["kost_eur"]), 2)}
                for m in self._c().get("maanden", [])]


class SefMaandpiek(_SefPiek):
    """De hoogste kwartierafname (kW) van deze maand tot nu, met wat die piek aan capaciteitstarief
    kost, en alle maanden van het voorbije jaar als attribuut. P1 houdt ze per kwartier bij."""

    _attr_native_unit_of_measurement = "kW"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        super().__init__(coordinator, "maandpiek", "maandpiek")

    def _deze(self) -> dict:
        maand = f"{dt_util.now():%Y-%m}"
        return next((m for m in self._maanden() if m["maand"] == maand), {})

    @property
    def native_value(self) -> float | None:
        return self._deze().get("piek_kw")

    @property
    def extra_state_attributes(self) -> dict:
        c = self._c()
        return {"kost_eur": self._deze().get("kost_eur"), "maanden": self._maanden(),
                "ondergrens_kw": _getal(c.get("ondergrens_kw")), "tarief_eur_kw_jaar": _getal(c.get("tarief_eur_kw_jaar"))}


class SefHoogstePiek(_SefPiek):
    """De hoogste maandpiek (kW) van de laatste 12 maanden, met de maand en haar kost."""

    _attr_native_unit_of_measurement = "kW"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        super().__init__(coordinator, "hoogste piek", "hoogste_piek")

    def _hoogste(self) -> dict:
        return max(self._maanden(), key=lambda m: m["piek_kw"], default={})

    @property
    def native_value(self) -> float | None:
        return self._hoogste().get("piek_kw")

    @property
    def extra_state_attributes(self) -> dict:
        h = self._hoogste()
        return {"maand": h.get("maand"), "kost_eur": h.get("kost_eur")}


class SefCapaciteitstarief(_SefPiek):
    """Wat je maandpieken van de laatste 12 maanden samen aan capaciteitstarief kosten (EUR incl.
    btw, op het tarief van nu; een maand zonder meting telt niet mee). Elke maandpiek telt 12
    maanden lang voor 1/12 mee in het gemiddelde dat Fluvius aanrekent: de kost van een piek is wat
    ze over die 12 facturen samen kost."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        super().__init__(coordinator, "capaciteitstarief", "capaciteitstarief")

    @property
    def native_value(self) -> float | None:
        return _getal(self._c().get("jaar_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        maanden = self._maanden()
        return {"maanden": len(maanden), "tarief_eur_kw_jaar": _getal(self._c().get("tarief_eur_kw_jaar")),
                "gemiddelde_piek_kw": None if not maanden else round(
                    sum(max(m["piek_kw"], _getal(self._c()["ondergrens_kw"])) for m in maanden) / len(maanden), 3)}


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


class SefGemiddeldePrijs(CoordinatorEntity, SensorEntity):
    """De gemiddelde all-in prijs per afgenomen kWh van je huidige contract over
    de periode van je laatste vergelijking: alles wat het kost (energie, vaste
    vergoeding, nettarief met capaciteitstarief, heffingen, btw) gedeeld door je
    afname; injectie niet verrekend. In c€/kWh, bewust niet EUR/kWh: dit is geen
    prijs voor het Energy-dashboard (daarvoor `sensor.sef_afnameprijs`, de prijs
    van een extra kWh). Onbekend zonder vergelijking die dit getal kent."""

    _attr_native_unit_of_measurement = "c€/kWh"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator)
        self._energie = energie
        self._attr_name = f"SEF gemiddelde prijs {energie}"
        self._attr_unique_id = f"sef_gemiddelde_prijs_{energie}"

    def _data(self) -> dict:
        return (self.coordinator.data or {}).get(self._energie) or {}

    @property
    def native_value(self) -> float | None:
        return _getal((self._data().get("huidig") or {}).get("gemiddelde_ct_kwh"))

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data()
        vergelijking, beste = d.get("vergelijking") or {}, d.get("beste") or {}
        reden = d.get("reden")
        if reden is None and self.native_value is None and d.get("huidig"):
            reden = "Uw laatste vergelijking is van vóór deze prijs: start een nieuwe vergelijking op de website."
        return {
            "contract": _naam(d.get("huidig")), "beste_contract": _naam(beste or None),
            "beste_gemiddelde_prijs": _getal(beste.get("gemiddelde_ct_kwh")),
            "periode_van": vergelijking.get("periode_van"), "periode_tot": vergelijking.get("periode_tot"),
            "vergeleken_op": vergelijking.get("afgerond_op"), "reden": reden,
        }


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
            # Sinds 0.8.0: de laatste 14 meetdagen en dezelfde weekdagen 52 weken eerder, zelfde prijzen.
            "laatste_14_dagen_kost": _getal(self._deel("recent").get("kost_eur")),
            "zelfde_dagen_vorig_jaar_kost": _getal(self._deel("recent_vorig_jaar").get("kost_eur")),
        }


class SefKostSindsAfrekening(_SefVoorschot):
    """Wat je verbruik kostte sinds je laatste afrekening (datum ingegeven op
    de website, Mijn voorschot), tot de laatste volledige meetdag, met het
    voorschot voor die periode (naar rato) en het verschil (positief = meer
    dan je voorschot). Elektriciteit en gas samen: een template in HA."""

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator, energie, "kost sinds afrekening", "kost_sinds_afrekening")

    @property
    def native_value(self) -> float | None:
        return _getal(self._deel("sinds_afrekening").get("kost_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        s = self._deel("sinds_afrekening")
        return {"van": s.get("van"), "tot": s.get("tot"), "voorschot": _getal(s.get("voorschot_eur")),
                "verschil": _getal(s.get("verschil_eur")), "reden": self._reden("sinds_afrekening")}


class SefJaarschatting(_SefVoorschot):
    """Wat je verbruik van de laatste 12 volle maanden kost met je huidige
    contract, met 12 × je voorschot en het aangeraden maandvoorschot. Voor gas
    ook de kost bij normaal weer (graaddagen van de 10 vorige jaren); het
    aangeraden voorschot volgt dan dat normale jaar."""

    def __init__(self, coordinator: DataUpdateCoordinator, energie: str) -> None:
        super().__init__(coordinator, energie, "jaarschatting", "jaarschatting")

    @property
    def native_value(self) -> float | None:
        return _getal(self._deel("jaar").get("kost_eur"))

    @property
    def extra_state_attributes(self) -> dict:
        j = self._deel("jaar")
        n = j.get("normaal") or {}  # enkel gas: bij normaal weer (sinds 0.7.0)
        return {
            "voorschot_jaar": _getal(j.get("voorschot_eur")), "saldo": _getal(j.get("saldo_eur")),
            "aangeraden_voorschot": _getal(j.get("aangeraden_voorschot_eur")),
            "van": j.get("van"), "tot": j.get("tot"), "contract": self._a().get("contract"),
            "reden": self._reden("jaar"), "waarschuwingen": j.get("waarschuwingen", []),
            "normaal_kost": _getal(n.get("kost_eur")), "normaal_saldo": _getal(n.get("saldo_eur")),
            "graaddagen": _getal(n.get("graaddagen")), "graaddagen_normaal": _getal(n.get("graaddagen_normaal")),
            "referentie": None if not n.get("referentie_van") else f"{n['referentie_van']}-{n['referentie_tot']}",
            "referentie_jaren": n.get("referentie_jaren"),
            "normaal_reden": n.get("reden"),
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
                "aangeraden_voorschot": _getal(j.get("aangeraden_voorschot_eur")), "reden": reden,
                "normaal_saldo": _getal((j.get("normaal") or {}).get("saldo_eur"))}
