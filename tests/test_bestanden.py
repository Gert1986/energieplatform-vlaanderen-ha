"""
test_bestanden.py
~~~~~~~~~~~~~~~~~

Zonder Home Assistant: elk Python-bestand van de integratie parset, `manifest.json` en `hacs.json` zijn
geldige JSON met een versie, de vertalingen hebben dezelfde sleutels, de logo's hebben de maten
van Home Assistant, en elke `from .module import naam` in de integratie bestaat echt.

:copyright: (c) 2026 Gert Botte
:license: MIT, zie LICENSE
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "custom_components" / "sef"
BESTANDEN = sorted(MAP.glob("*.py"))


@pytest.mark.parametrize("pad", BESTANDEN, ids=lambda p: p.name)
def test_relatieve_imports_bestaan(pad):
    """Een hernoemde of vergeten functie breekt anders pas in Home Assistant zelf."""
    for knoop in ast.walk(ast.parse(pad.read_text(encoding="utf-8"))):
        if isinstance(knoop, ast.ImportFrom) and knoop.level == 1 and knoop.module:
            doel = ast.parse((MAP / f"{knoop.module}.py").read_text(encoding="utf-8"))
            namen = {n.name for n in doel.body if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))}
            namen |= {t.id for n in doel.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
            ontbreekt = {a.name for a in knoop.names} - namen
            assert not ontbreekt, f"{pad.name}: {knoop.module} mist {ontbreekt}"


def test_manifest_en_hacs_zijn_geldige_json_met_versie():
    manifest = json.loads((MAP / "manifest.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]) and manifest["domain"] == "sef"
    assert json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))


def _sleutels(d: dict, pad: str = "") -> set[str]:
    return {k for n, v in d.items() for k in (_sleutels(v, f"{pad}{n}.") if isinstance(v, dict) else {f"{pad}{n}"})}


def test_vertalingen_hebben_dezelfde_sleutels():
    """Een tekst die enkel in nl.json staat, toont Home Assistant in het Engels als ruwe sleutel."""
    nl, en = (_sleutels(json.loads((MAP / "translations" / f"{t}.json").read_text(encoding="utf-8"))) for t in ("nl", "en"))
    assert nl == en, f"enkel nl: {nl - en}, enkel en: {en - nl}"


@pytest.mark.parametrize("naam,breed,hoog", [("icon.png", 256, 256), ("icon@2x.png", 512, 512),
                                              ("logo.png", None, 128), ("logo@2x.png", None, 256)])
def test_logo_maten(naam, breed, hoog):
    """Home Assistant toont `brand/` enkel met de vaste maten (icoon vierkant, logo 128/256 hoog)."""
    kop = (MAP / "brand" / naam).read_bytes()[:24]
    assert kop[:8] == b"\x89PNG\r\n\x1a\n"
    b, h = int.from_bytes(kop[16:20], "big"), int.from_bytes(kop[20:24], "big")
    assert h == hoog and b == (breed or b)
