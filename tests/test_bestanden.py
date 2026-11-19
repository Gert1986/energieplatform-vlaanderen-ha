"""
test_bestanden.py
~~~~~~~~~~~~~~~~~

Zonder Home Assistant: elk Python-bestand van de integratie parset, `manifest.json` en `hacs.json` zijn
geldige JSON met een versie, en elke `from .module import naam` in de integratie bestaat echt.

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
