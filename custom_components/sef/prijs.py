"""
prijs.py
~~~~~~~~

Zuivere hulpfuncties voor de prijssensoren (geen Home Assistant nodig, dus testbaar): het getal uit
een SEF-tekst en de opbouw van de prijs van nu uit `/mij/prijs`. Sinds 0.18.0 de prijssignalen: de rang
van het kwartier van nu en het goedkoopste aaneengesloten venster, gerekend in `Decimal` uit de strings
van SEF.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

KWARTIER = timedelta(minutes=15)


def getal(waarde: str | None) -> float | None:
    """SEF stuurt bedragen als decimale tekst; None blijft None."""
    return None if waarde is None else float(waarde)


def opbouw_attribuut(data: dict, richting: str) -> dict:
    """Het attribuut `opbouw` van een prijssensor, als `{"opbouw": {post: EUR/kWh}}`, of `{}` als SEF
    het niet meestuurt (oudere server, of injectie zonder spotprijs). Afname: energie, net, heffingen,
    btw. Injectie: beurs en marge. Zo blijft de sensor werken tegen een server zonder dit veld."""
    opbouw = data.get("opbouw" if richting == "afname" else "opbouw_injectie")
    return {"opbouw": {post: getal(bedrag) for post, bedrag in opbouw.items()}} if opbouw else {}


def prijs_nu(data: dict, richting: str, nu: datetime, kwartier) -> float | None:
    """De prijs van het kwartier waarin `nu` valt, uit `kwartieren`, zodat de sensor met de kloktijd
    meeloopt en niet achterblijft tot de volgende opvraging (tot 15 min later). Zonder kwartieren (of
    buiten het bereik, bv. na de maandgrens) valt ze terug op `<richting>_nu` uit de laatste opvraging.
    `kwartier` is `p1.kwartier_nu`; als parameter zodat deze module geen eigen importpad nodig heeft."""
    k = kwartier(data.get("kwartieren") or [], nu)
    return getal(k[richting]) if k is not None and k.get(richting) is not None else getal(data.get(f"{richting}_nu"))


def horizon(kwartieren: list[dict], vanaf: datetime) -> list[tuple[datetime, Decimal]]:
    """De afnameprijzen van het kwartier waarin `vanaf` valt (of het eerste erna) tot het laatst gekende.
    Enkel opeenvolgend, 15 minuten na elkaar in absolute tijd (dus ook over zomer- en wintertijd): bij een
    gat of een kwartier zonder prijs stopt de horizon. SEF stopt zelf op de maandgrens."""
    uit: list[tuple[datetime, Decimal]] = []
    for start, prijs in sorted(((datetime.fromisoformat(k["tijdstip"]), k.get("afname")) for k in kwartieren),
                               key=lambda r: r[0]):
        if start + KWARTIER <= vanaf:
            continue
        if prijs is None or (uit and start != uit[-1][0] + KWARTIER):
            break
        uit.append((start, Decimal(prijs)))
    return uit


def rang(kwartieren: list[dict], nu: datetime) -> int | None:
    """0 tot 100: het percentage kwartieren in de horizon dat strikt goedkoper is dan dat van nu (B3).
    Bij dag/nacht hebben alle nachtkwartieren 0. None zonder het kwartier van nu of met minder dan 2."""
    h = horizon(kwartieren, nu)
    if len(h) < 2 or h[0][0] > nu:
        return None
    return 100 * sum(prijs < h[0][1] for _, prijs in h) // len(h)


def goedkoopste_venster(kwartieren: list[dict], nu: datetime, aantal: int) -> dict | None:
    """Het aaneengesloten blok van `aantal` kwartieren met de laagste gemiddelde afnameprijs, ten vroegste
    vanaf het kwartier van nu. Bij gelijke prijs het vroegste. None als de horizon te kort is. Dat een
    begonnen blok blijft staan, regelt de sensor (die onthoudt het lopende blok)."""
    h = horizon(kwartieren, nu)
    if len(h) < aantal:
        return None
    som, i = min((sum(p for _, p in h[i:i + aantal]), i) for i in range(len(h) - aantal + 1))
    return {"begin": h[i][0], "einde": h[i][0] + aantal * KWARTIER, "gemiddelde": som / aantal}
