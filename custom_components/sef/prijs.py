"""
prijs.py
~~~~~~~~

Zuivere hulpfuncties voor de prijssensoren (geen Home Assistant nodig, dus testbaar): het getal uit
een SEF-tekst en de opbouw van de prijs van nu uit `/mij/prijs`.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations


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
