"""
piek.py
~~~~~~~

Peak Guard (sinds 0.19.0), zuiver en zonder Home Assistant: het gemiddelde afnamevermogen van het lopende
kwartier uit de telwerken, de verwachte kwartierpiek, de drempel (de hoogste maandpiek tot nu of de
ondergrens) en wat een nieuwe piek kost. Het capaciteitstarief rekent per maand max(piek, ondergrens) ×
jaartarief / 12, net als SEF in `/mij/prijs` → `capaciteit.maanden[].kost_eur`. Enkel signalen: niets stuurt.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

KWARTIER = timedelta(minutes=15)
UUR = Decimal(3600)


def _seconden(duur: timedelta) -> Decimal:
    return Decimal(str(duur.total_seconds()))


def _kw(kwh: Decimal, duur: timedelta) -> Decimal:
    """kWh over een duur als gemiddeld kW; eerst vermenigvuldigen, zodat 1/60 uur geen afrondingsrest geeft."""
    return kwh * UUR / _seconden(duur)


def kwartierstand(telwerk_begin: Decimal | None, telwerk_nu: Decimal | None, begin: datetime | None,
                  nu: datetime) -> Decimal | None:
    """Het gemiddelde afnamevermogen (kW) van `begin` tot `nu`, uit het verschil op de afnametelwerken (kWh).
    None zonder beginstand (na een herstart midden in het kwartier), zonder stand nu (telwerk onbeschikbaar),
    bij een telwerk dat terugloopt, op de kwartiergrens zelf of als `begin` niet in dit kwartier ligt."""
    if telwerk_begin is None or telwerk_nu is None or begin is None or telwerk_nu < telwerk_begin:
        return None
    if not timedelta(0) < nu - begin <= KWARTIER:
        return None
    return _kw(telwerk_nu - telwerk_begin, nu - begin)


def verwachte_piek(gemiddeld: Decimal | None, vermogen_nu: Decimal | None, verstreken: timedelta) -> Decimal | None:
    """Het kwartiergemiddelde als het vermogen van nu aanhoudt tot het einde van het kwartier (kW). Een
    negatief vermogen (injectie) telt als 0: de piek telt enkel afname. Zonder vermogen: het gemiddelde."""
    if gemiddeld is None:
        return None
    if vermogen_nu is None:
        return gemiddeld
    deel = min(_seconden(verstreken) / _seconden(KWARTIER), Decimal(1))
    return gemiddeld * deel + max(vermogen_nu, Decimal(0)) * (1 - deel)


def drempel(maandpiek_kw: Decimal | None, lokaal_kw: Decimal | None, ondergrens_kw: Decimal | None,
            doel_kw: Decimal | None = None) -> tuple[Decimal | None, str | None]:
    """Vanaf welk kwartiergemiddelde een piek iets kost (of het eigen doel overschreden is): het hoogste van
    de maandpiek uit SEF, de hoogste kwartierpiek die de integratie zelf deze maand zag (SEF kan achterlopen),
    de ondergrens en het doel. Met de bron erbij. (None, None) zonder enig gegeven."""
    kandidaten = [(w, bron) for w, bron in ((maandpiek_kw, "maandpiek"), (lokaal_kw, "lokaal"),
                                             (ondergrens_kw, "ondergrens"), (doel_kw, "doel")) if w is not None]
    return max(kandidaten, key=lambda k: k[0]) if kandidaten else (None, None)


def kost_nieuwe_piek(verwacht_kw: Decimal | None, drempel_kw: Decimal | None,
                     tarief_eur_kw_jaar: Decimal | None) -> Decimal | None:
    """Wat de verwachte piek deze maand extra kost aan capaciteitstarief (EUR incl. btw, zoals SEF): het deel
    boven de drempel × jaartarief / 12. 0 als ze onder de drempel blijft."""
    if verwacht_kw is None or drempel_kw is None or tarief_eur_kw_jaar is None:
        return None
    return max(verwacht_kw - drempel_kw, Decimal(0)) * tarief_eur_kw_jaar / 12


def vermogen_uit_telwerk(vorige: tuple[datetime, Decimal] | None, nu: tuple[datetime, Decimal]) -> Decimal | None:
    """Het vermogen (kW) tussen twee telwerkstanden (de vorige minuut en nu), voor wie geen vermogenssensor
    opgeeft. None zonder vorige stand, bij een telwerk dat terugloopt of een vorige stand van te lang geleden."""
    if vorige is None or nu[1] < vorige[1] or not timedelta(0) < nu[0] - vorige[0] <= KWARTIER:
        return None
    return _kw(nu[1] - vorige[1], nu[0] - vorige[0])
