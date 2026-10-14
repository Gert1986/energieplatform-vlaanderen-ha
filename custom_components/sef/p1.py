"""
p1.py
~~~~~

Van de 5-minutenstatistieken van de P1-telwerken in Home Assistant naar de
kwartierrijen (elektriciteit, ADR-0054) en uurrijen in m³ (gas, ADR-0064) die
SEF verwacht. Puur: geen Home Assistant nodig.

:copyright: (c) 2026 Gert Botte
:license: Alle rechten voorbehouden
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

KWARTIER = timedelta(minutes=15)
UUR = timedelta(hours=1)
BLOK = timedelta(minutes=5)
MAX_RIJEN = 1000          # per aanvraag aan SEF
MAX_KWH = Decimal("25")   # per kwartier, zoals SEF
MAX_M3 = Decimal("10")    # per uur, zoals SEF (een G6-gasmeter)
KANALEN = ("afname_1", "afname_2", "injectie_1", "injectie_2")


def kwartiergrens(t: datetime) -> datetime:
    """Begin van het kwartier waarin `t` valt (UTC)."""
    t = t.astimezone(timezone.utc)
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def uurgrens(t: datetime) -> datetime:
    """Begin van het uur waarin `t` valt (UTC)."""
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def gasrijen(blokken: dict[datetime, float], *, vanaf: datetime, tot: datetime) -> list[dict]:
    """Rijen `{tijdstip, m3}` voor elk uur van [vanaf, tot), uit het
    gasverbruik per 5-minutenblok (begin -> m³, de `change` van de
    statistiek). SEF rekent zelf om naar kWh met de factor uit je
    Fluvius-export. Een ontbrekend blok telt als 0 (zie `kwartierrijen`);
    een negatief blok of meer dan `MAX_M3` in een uur stopt alles (ValueError)."""
    rijen: list[dict] = []
    u = uurgrens(vanaf)
    while u + UUR <= tot:
        waarden = [Decimal(repr(blokken.get(u + i * BLOK, 0.0))) for i in range(12)]
        if any(w < 0 for w in waarden):
            raise ValueError(f"gas: negatief verbruik rond {u.isoformat()} (telwerk vervangen of teruggezet?).")
        m3 = sum(waarden, Decimal("0")).quantize(Decimal("0.000001"))
        if m3 > MAX_M3:
            raise ValueError(f"gas: {m3} m³ in het uur van {u.isoformat()} is onmogelijk.")
        rijen.append({"tijdstip": u.isoformat(), "m3": format(m3, "f")})
        u += UUR
    return rijen


def kwartierrijen(blokken: dict[str, dict[datetime, float]], *, vanaf: datetime, tot: datetime,
                  tarief_1: str = "dag", enkelvoudig: bool = False) -> list[dict]:
    """Rijen `{tijdstip, register, richting, kwh}` voor elk kwartier van
    [vanaf, tot), uit `blokken`: per kanaal (`KANALEN`) het verbruik per
    5-minutenblok (begin -> kWh, de `change` van de statistiek).

    Per richting en kwartier één register (SEF weigert er twee): het telwerk
    dat bewoog. Bewogen beide (een tariefwissel binnen het kwartier), dan
    alles op het grootste. Bewoog geen van beide, dan het register van het
    vorige kwartier: bij 0 kWh maakt het voor de prijs niets uit.

    Een ontbrekend blok (Home Assistant lag stil) telt als 0: de statistiek
    zet dat verbruik dan in het eerste blok na de herstart. Het totaal klopt,
    het tijdstip niet; een latere Fluvius-export zet het recht.
    Een negatief blok is een telwerkfout en stopt alles (ValueError)."""
    tarief_2 = "nacht" if tarief_1 == "dag" else "dag"
    vorig = {"afname": tarief_1, "injectie": tarief_1}
    rijen: list[dict] = []
    q = kwartiergrens(vanaf)
    while q + KWARTIER <= tot:
        for richting in ("afname", "injectie"):
            som = []
            for tarief in ("1", "2"):
                kanaal = blokken.get(f"{richting}_{tarief}", {})
                waarden = [Decimal(repr(kanaal.get(q + i * BLOK, 0.0))) for i in range(3)]
                if any(w < 0 for w in waarden):
                    raise ValueError(f"{richting} tarief {tarief}: negatief verbruik rond {q.isoformat()} "
                                     "(telwerk vervangen of teruggezet?).")
                som.append(sum(waarden, Decimal("0")))
            kwh = (som[0] + som[1]).quantize(Decimal("0.000001"))
            if kwh > MAX_KWH:
                raise ValueError(f"{richting}: {kwh} kWh in het kwartier van {q.isoformat()} is onmogelijk.")
            if enkelvoudig:
                register = "enkelvoudig"
            elif som[0] or som[1]:
                register = tarief_1 if som[0] >= som[1] else tarief_2
            else:
                register = vorig[richting]
            vorig[richting] = register
            rijen.append({"tijdstip": q.isoformat(), "register": register, "richting": richting,
                          "kwh": format(kwh, "f")})
        q += KWARTIER
    return rijen


def in_stukken(rijen: list[dict], grootte: int = MAX_RIJEN) -> list[list[dict]]:
    """Opeenvolgende stukken van hoogstens `grootte` rijen, oudste eerst: SEF
    aanvaardt enkel wat zonder gat op zijn laatste meting aansluit. Een
    kwartier (afname + injectie) wordt nooit gesplitst."""
    if grootte % 2:
        raise ValueError("grootte moet even zijn: twee rijen per kwartier")
    return [rijen[i:i + grootte] for i in range(0, len(rijen), grootte)]
