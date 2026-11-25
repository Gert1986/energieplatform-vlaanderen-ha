"""
planner.py
~~~~~~~~~~

De batterijplanner (sinds 0.20.0, ADR-0001: lokaal plannen), zuiver en zonder Home Assistant. Beslist voor het
kwartier van nu: van het net laden of de batterij haar eigen gang laten gaan (zelfverbruik). Enkel met de
afnameprijzen van SEF, nooit een eigen prijsberekening.

Laden als het kwartier van nu bij de goedkoopste kwartieren hoort die nodig zijn om de batterij te vullen, en
de prijs na verlies nog onder het gemiddelde van de duurste kwartieren erna ligt. Het laadvermogen blijft binnen
de piekruimte van Peak Guard; zonder gekende piekruimte geen laadadvies. Ruimte voor zonne-overschot: wat
gisteren geïnjecteerd en in de batterij geladen werd, blijft vrij (een naïeve schatting).

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from .prijs import horizon

RENDEMENT = Decimal("0.85")  # ponytail: vast rendement heen en terug; een instelling als iemand het meet
KWARTIER_H = Decimal("0.25")


def _eur(p: Decimal) -> str:
    return f"{p:.3f}".replace(".", ",")


def beslis(kwartieren: list[dict], nu: datetime, *, soc_pct: Decimal | None, capaciteit_kwh: Decimal,
           laadvermogen_kw: Decimal, max_soc_pct: Decimal = Decimal(100), piekruimte_kw: Decimal | None,
           zon_kwh: Decimal = Decimal(0)) -> dict:
    """{"actie": "laden" of "zelfverbruik", "reden": vaste korte categorie, "vermogen_kw", "detail"}. `reden` is
    kort en wisselt weinig (voor het logboek); `detail` draagt de prijzen en tijden."""
    def uit(reden: str, detail: str = "") -> dict:
        return {"actie": "zelfverbruik", "reden": reden, "vermogen_kw": Decimal(0), "detail": detail}

    h = horizon(kwartieren, nu)
    if not h or h[0][0] > nu:
        return uit("geen prijs")
    if soc_pct is None:
        return uit("laadtoestand onbekend")
    tekort = (max_soc_pct - soc_pct) / 100 * capaciteit_kwh - zon_kwh
    if tekort <= 0:
        return uit("vol genoeg", f"laadtoestand {soc_pct}%, ruimte voor zon {zon_kwh} kWh")
    nodig = math.ceil(tekort / (laadvermogen_kw * KWARTIER_H))
    # Laden vóór het duurste kwartier van de komende 24 uur: anders wacht hij na 13 u op de goedkope middag
    # van morgen en is de batterij leeg voor de piek van vanavond.
    # ponytail: één piek per dag; meerdere pieken (ochtend en avond) als de proefdraai dat vraagt
    dag = h[:96]
    piek = max(range(len(dag)), key=lambda i: dag[i][1])
    goedkoopste = sorted(h[:piek], key=lambda k: (k[1], k[0]))[:nodig]
    prijs_nu = h[0][1]
    if not goedkoopste:
        return uit("nu duurst", f"nu {_eur(prijs_nu)} €/kWh, het duurste kwartier van de komende 24 uur")
    if h[0] not in goedkoopste:
        goedkoopst = min(goedkoopste, key=lambda k: k[1])
        return uit("later goedkoper", f"nu {_eur(prijs_nu)} €/kWh, om {goedkoopst[0]:%H:%M} {_eur(goedkoopst[1])}")
    later = sorted((p for _, p in h[piek:]), reverse=True)[:nodig]
    if not later:
        return uit("verschil te klein", "geen latere prijzen")
    duur = sum(later) / len(later)
    if prijs_nu >= duur * RENDEMENT:
        return uit("verschil te klein", f"nu {_eur(prijs_nu)} €/kWh, later hoogstens {_eur(duur)}")
    if piekruimte_kw is None:
        return uit("piekruimte onbekend")
    vermogen = min(laadvermogen_kw, max(piekruimte_kw, Decimal(0)))
    if vermogen <= 0:
        return uit("geen piekruimte", f"piekruimte {piekruimte_kw:.2f} kW")
    return {"actie": "laden", "reden": "goedkoop kwartier", "vermogen_kw": vermogen,
            "detail": f"nu {_eur(prijs_nu)} €/kWh, later gemiddeld {_eur(duur)}, {nodig} kwartieren nodig"}
