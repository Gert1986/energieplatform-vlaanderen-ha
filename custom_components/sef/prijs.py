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
