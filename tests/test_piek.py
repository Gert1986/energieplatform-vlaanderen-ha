"""
test_piek.py
~~~~~~~~~~~~

`custom_components/sef/piek.py` (Peak Guard, sinds 0.19.0): kwartiervermogen uit de telwerken, de verwachte
piek, de drempel en de kost van een nieuwe piek, nagerekend tegen de capaciteitsberekening van SEF.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "sef"))
from piek import drempel, kost_nieuwe_piek, kwartierstand, vermogen_uit_telwerk, verwachte_piek  # noqa: E402

TIEN = datetime.fromisoformat("2026-11-20T10:00:00+01:00")


def test_constante_last():
    gemiddeld = kwartierstand(D("100"), D("100.25"), TIEN, TIEN + timedelta(minutes=5))  # 0,25 kWh in 5 min
    assert gemiddeld == D("3")
    assert verwachte_piek(gemiddeld, D("3"), timedelta(minutes=5)) == D("3")
    assert vermogen_uit_telwerk((TIEN + timedelta(minutes=4), D("100.2")), (TIEN + timedelta(minutes=5), D("100.25"))) == D("3")


def test_laststap_halverwege_het_kwartier():
    gemiddeld = kwartierstand(D("100"), D("100.125"), TIEN, TIEN + timedelta(minutes=7, seconds=30))  # 1 kW
    assert gemiddeld == D("1")
    assert verwachte_piek(gemiddeld, D("5"), timedelta(minutes=7, seconds=30)) == D("3")  # helft 1, helft 5
    assert verwachte_piek(gemiddeld, D("-2"), timedelta(minutes=7, seconds=30)) == D("0.5")  # injectie telt als 0
    assert verwachte_piek(gemiddeld, None, timedelta(minutes=7, seconds=30)) == D("1")  # zonder vermogen


def test_kwartiergrens():
    assert kwartierstand(D("100"), D("100"), TIEN, TIEN) is None  # op de grens zelf
    assert kwartierstand(D("100"), D("101"), TIEN, TIEN + timedelta(minutes=15)) == D("4")  # het volle kwartier
    assert kwartierstand(D("100"), D("101"), TIEN, TIEN + timedelta(minutes=16)) is None  # begin van een vorig kwartier
    assert verwachte_piek(D("2"), D("6"), timedelta(minutes=15)) == D("2")  # voorbij: enkel wat gemeten is


def test_zomer_en_wintertijd_in_absolute_tijd():
    # 29 maart: 01:45 + 15 min = 03:00 zomertijd. 25 oktober: 02:45 zomertijd + 15 min = 02:00 wintertijd.
    for begin, nu in (("2026-03-29T01:45:00+01:00", "2026-03-29T03:00:00+02:00"),
                      ("2026-10-25T02:45:00+02:00", "2026-10-25T02:00:00+01:00")):
        assert kwartierstand(D("10"), D("10.5"), datetime.fromisoformat(begin), datetime.fromisoformat(nu)) == D("2")
    # De tweede 02:45 (wintertijd) is een uur na de eerste: geen kwartier.
    assert kwartierstand(D("10"), D("10.5"), datetime.fromisoformat("2026-10-25T02:45:00+02:00"),
                         datetime.fromisoformat("2026-10-25T02:50:00+01:00")) is None


def test_onbeschikbaar_telwerk_en_herstart_midden_in_een_kwartier():
    assert kwartierstand(D("100"), None, TIEN, TIEN + timedelta(minutes=5)) is None  # telwerk onbeschikbaar
    assert kwartierstand(None, D("100.2"), TIEN, TIEN + timedelta(minutes=5)) is None  # geen beginstand
    assert kwartierstand(D("100"), D("99"), TIEN, TIEN + timedelta(minutes=5)) is None  # telwerk vervangen
    assert verwachte_piek(None, D("3"), timedelta(minutes=5)) is None
    assert vermogen_uit_telwerk(None, (TIEN, D("100"))) is None


def test_drempel_ondergrens_lokaal_en_doel():
    assert drempel(None, None, D("2.5")) == (D("2.5"), "ondergrens")  # begin van de maand: nog geen piek in SEF
    assert drempel(D("1.9"), None, D("2.5")) == (D("2.5"), "ondergrens")
    assert drempel(D("3.95"), None, D("2.5")) == (D("3.95"), "maandpiek")
    assert drempel(D("3.95"), D("4.2"), D("2.5")) == (D("4.2"), "lokaal")  # SEF loopt achter
    assert drempel(D("3.95"), None, D("2.5"), D("5")) == (D("5"), "doel")
    assert drempel(None, None, None) == (None, None)


def test_kost_nieuwe_piek_zoals_sef():
    # SEF: maand met piek 1,9 kW (telt als ondergrens 2,5) kost 11.0416666667, met 3,95 kW 17.4458333333.
    tarief = D("53.0")
    kost = kost_nieuwe_piek(D("3.95"), D("2.5"), tarief)
    assert kost.quantize(D("0.0000001")) == (D("17.4458333333") - D("11.0416666667")).quantize(D("0.0000001"))
    assert kost_nieuwe_piek(D("2"), D("2.5"), tarief) == 0
    assert kost_nieuwe_piek(D("3"), None, tarief) is None and kost_nieuwe_piek(D("3"), D("2.5"), None) is None
