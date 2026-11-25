# Implementatieplan EMS (bij ADR-0001)

Concept, 2026-11-24. Werkt [ADR-0001](adr/ADR-0001-ems-lokaal-plannen.md) en de
[EMS-roadmap v1.0](ems-roadmap-v1.0.txt) uit tot releases. Elke release is op zichzelf bruikbaar en
draait lokaal. Er komen geen nieuwe persoonsgegevens naar SEF. Sturing van apparaten zit er nog niet in, die
volgt na de beslissing over ADR-0001.

| Release | Inhoud | Serverwijziging | Nieuwe data naar SEF |
|---|---|---|---|
| 0.16.0 | Repairs, diagnostics aanvullen | nee | nee |
| 0.17.0 | Reconfigure-flow | nee | nee |
| 0.18.0 | Prijssignalen (rang, goedkoop/duur, goedkoopste vensters) | nee (maandgrens: zie S1) | nee |
| 0.19.0 | Peak Guard (lokaal, enkel signalen) | nee | nee |
| daarna | Beslissing ADR-0001, dan planner en modi | open | volgens ADR |

## Beslispunten (met voorstel)

Elk punt heeft een voorstel waarmee het plan verder kan. Gert beslist vóór de release die erop staat.

- **B1 YAML tegenover de UI** (vóór 0.17.0). Voorstel: een ingave uit de YAML (`entry.source == import`)
  blijft door de YAML beheerd. Reconfigure breekt dan af met "beheerd via configuration.yaml". Zonder YAML
  werkt reconfigure gewoon. Geen gedrag dat stil terugzet.
- **B2 Taal van de nieuwe entiteiten** (vóór 0.18.0). Voorstel: Nederlands, zoals de bestaande
  (`sensor.sef_prijsrang`, `binary_sensor.sef_goedkoop_nu`, `sensor.sef_goedkoopste_1u`). Engelse vertaling via
  `translations/en.json`.
- **B3 "Goedkoop" bij een niet-dynamisch contract** (vóór 0.18.0). Voorstel: geen speciaal geval. De rang
  is het percentage kwartieren in de horizon dat **strikt goedkoper** is. Bij dag/nacht hebben alle
  nachtkwartieren rang 0 (goedkoop) en de dagkwartieren een hogere rang. Het attribuut `prijsniveaus` (het
  aantal verschillende prijzen) toont dat er maar twee zijn.
- **B4 Maandgrens** (vóór S1). Voorstel: SEF geeft na het einde van de maand kwartieren met
  `raming: true`, gerekend met de formule van het huidige contract op de day-ahead-prijs (dynamisch) of met
  dezelfde dag/nachtprijs (vast en variabel). Dat is een API- en semantiekwijziging in SEF, dus een SEF-ADR.
- **B5 Batterij beschrijfbaar?** (vóór fase 3). Heeft Gerts batterij een lokale HA-integratie waarmee je
  modus en vermogen kunt **schrijven**, met terugmelding? Zonder zo'n integratie blijft elke batterijsturing
  dry-run.

## 0.16.0: Repairs en diagnostics

**Stand (2026-11-25):** gebouwd, tests groen, nog niet gecommit. In Gerts HA (2026.11.3) getest: diagnose correct gemaskeerd, de drie uploads lukken, Repair bij `P1_GEEN_TOESTEMMING` verschijnt om 11:02 en verdwijnt na de upload van 11:32. Nog open: een dag draaien zonder valse Repair.

**Doel:** een fout die de gebruiker kan oplossen, verschijnt als Repair. Een tijdelijke fout niet. Het
diagnosebestand toont ook de uploads.

Wijzigingen:

1. `__init__.py` `_taak`: deel de fouten in.
   - `SefFout` met `status == 0` (codes van de client: `GEEN_AANSLUITING`, `AANSLUITING`,
     `GEEN_FLUVIUS_DATA`, `GAT_NA_LAATSTE_METING`) of 4xx behalve 408 en 429 → `issue_registry.async_create_issue`
     (`is_fixable=False`, één `translation_key` `upload_geweigerd` met code en titel van SEF als placeholder:
     de titel zegt al wat te doen, dus geen tekst per code).
   - 5xx, 408, 429, `ClientError` en `TimeoutError` → enkel een waarschuwing in het logboek en een nieuwe poging
     in het volgende venster (zoals nu bij netwerkfouten).
   - `ValueError` uit `p1.py` (telwerk zonder statistiek) → Repair `telwerkfout` (ook bij een negatieve of onmogelijke waarde; de tekst uit `p1.py` zegt welke).
   - Een geslaagde upload verwijdert de Repair (`async_delete_issue`).
   - `persistent_notification` valt weg.
   - `SefClient.vraag` leest de foutbody pas na de status: een HTML-foutpagina bij een 502 was een
     `ValueError` en dus een valse melding "telwerk".
   - Repairs zijn niet blijvend (`is_persistent=False`) en verdwijnen bij het verwijderen van de integratie.
   - Een onbekende code krijgt een algemene Repair met de titel van SEF, zodat een nieuwe servercode nooit stil
     verdwijnt.
2. Per taak de status bijhouden in `hass.data[DOMAIN][entry_id]["uploads"][naam]`: laatste poging, laatste
   succes en laatste foutcode (geen fouttekst met data).
3. `diagnostics.py`:
   - de versie van HA (`homeassistant.const.__version__`);
   - de uploadstatus uit punt 2;
   - `url` maskeren als die afwijkt van `STANDAARD_URL`;
   - de entity-ID's van de telwerken maskeren, zodat enkel zichtbaar blijft dat een sleutel ingesteld is
     (een entity-ID kan een naam bevatten).
4. `translations/nl.json` en `en.json`: een `issues`-sectie per code.

Tests:

- `test_upload.py`:
  - 403 `P1_GEEN_TOESTEMMING` maakt een Repair, de volgende geslaagde upload haalt hem weg;
  - 503 en 429 maken geen Repair;
  - een onbekende 4xx-code maakt een algemene Repair.
- `test_config_flow.py`: de diagnosetest vergelijkt het **volledige** dict met een verwacht dict
  (momentopname), met een eigen tailnet-URL en een telwerk-ID die er niet in mogen staan.

Klaar wanneer: geen `persistent_notification` meer in de code, de tests groen, en Gerts eigen HA
draait een dag zonder valse Repair.

## 0.17.0: Reconfigure

**Stand (2026-11-25):** gebouwd en getest (`test_config_flow.py` groen), nog niet in Gerts HA. Op Gerts vraag in
dezelfde werkmap als 0.16.0 gebouwd: één commit en één release 0.17.0 met beide. B1 volgens het voorstel
gebouwd, nog te bevestigen. De sleutel staat nooit leesbaar in het formulier: leeg laten houdt de huidige.
Open bij B1: een ingave die ooit uit de YAML kwam en waarvan het `sef:`-blok weg is, blijft `import` en kan
dus niet via de UI; verwijderen en opnieuw toevoegen werkt wel.

**Doel:** telwerken, sleutel of meter wijzigen zonder de integratie te verwijderen.

Wijzigingen:

1. `config_flow.py`: `async_step_reconfigure`.
   - Dezelfde twee stappen als `user` en `meter`, ingevuld met de huidige waarden.
   - Het formulierschema wordt een gedeelde functie in plaats van twee kopieën.
   - Een ingave uit de YAML breekt af met `yaml_beheerd` (B1).
   - Opslaan met `async_update_reload_and_abort`.
2. Geen `VERSION`-verhoging en geen `async_migrate_entry`: de opbouw van `entry.data` verandert niet. De
   `unique_id` van de entiteiten blijft gelijk, dus automatiseringen blijven werken. Een migratie komt pas
   als het schema wijzigt.
3. Vertalingen: stap `reconfigure`, afbreekredenen `yaml_beheerd` en `reconfigure_successful`.

Tests in `test_config_flow.py`:

- een telwerk toevoegen of weghalen;
- een ongeldige sleutel geeft een fout en de oude data blijft;
- een ingave uit de YAML breekt af;
- na het herladen hebben de entiteiten dezelfde `unique_id`.

## 0.18.0: Prijssignalen

**Stand (2026-11-25):** gebouwd en getest (`test_prijs.py`, `test_config_flow.py`, `test_upload.py` groen),
nog niet in Gerts HA, nog niet gecommit, nog niets op de website (0.17.0 is vrijgegeven op 2026-11-25). B2 en B3 volgens het voorstel, nog te bevestigen. `rang` kent enkel de afname (geen
`richting`: geen entiteit gebruikt injectie). `goedkoopste_venster` kijkt vanaf het kwartier van nu; de
sensor onthoudt het lopende blok, zodat het niet verschuift zolang het loopt (na een herstart vergeten). `binary_sensor` start na `sensor`
(leent de coördinator). S1 (maandgrens) blijft open.

**Doel:** automatiseringen zonder YAML-lussen over `kwartieren`. Volledig lokaal, uit de bestaande
`/mij/prijs`.

Zuivere functies in `prijs.py` (zonder HA, zoals `prijs_nu`):

- `horizon(kwartieren, nu)`: de kwartieren van het huidige kwartier tot het laatst gekende, enkel opeenvolgend
  (15 minuten na elkaar, in absolute tijd). Bij een gat stopt de horizon.
- `rang(kwartieren, nu, richting)`: 0 tot 100, het percentage kwartieren in de horizon dat strikt goedkoper is
  dan het huidige (B3). Met minder dan 2 kwartieren is de rang `None`.
- `goedkoopste_venster(kwartieren, nu, aantal)`: het aaneengesloten blok van `aantal` kwartieren (4, 8 of 16)
  met de laagste gemiddelde afnameprijs, met begin, einde en gemiddelde. Als de horizon te kort is: `None`. Een
  uur is altijd 4 kwartieren, ook op een dag met zomer- of wintertijd.
- Rekenen in `Decimal` uit de strings van SEF. Pas `float` voor de toestand van de sensor.

Entiteiten (namen volgens B2, `unique_id` stabiel):

- `sensor.sef_prijsrang`, met de attributen `horizon_tot` en `prijsniveaus`;
- `binary_sensor.sef_goedkoop_nu` (rang ≤ 25) en `binary_sensor.sef_duur_nu` (rang ≥ 75). De drempels zijn
  constanten, een optie volgt pas als iemand erom vraagt;
- `sensor.sef_goedkoopste_1u`, `_2u` en `_4u`: `device_class: timestamp` met het begin, en als attributen
  `einde` en `gemiddelde_prijs`. Ze schuiven niet mee als het venster begonnen is: dat geeft het blok dat nu
  loopt;
- een nieuw platform `binary_sensor` in `PLATFORMS`, dezelfde coordinator `SEF prijs` en `_Kwartierklok` voor
  de tik op de kwartiergrens.

Overgeslagen: Energy Score, want dat is dezelfde rang onder een andere naam (ADR-0001). Blueprints zijn
overbodig: het vaatwasservoorbeeld in de README gaat naar `sef_goedkoopste_2u`.

Tests in `test_prijs.py`:

- dagen met 96, 92 en 100 kwartieren;
- dag/nacht met gelijke prijzen (B3);
- negatieve prijzen;
- een horizon korter dan het venster;
- een gat in `kwartieren`;
- de laatste dag van de maand (de horizon stopt om middernacht).

En één HA-test: de entiteiten bestaan en `goedkoop_nu` springt op de kwartiergrens.

### S1 (SEF, apart): kwartieren voorbij de maandgrens

Na B4: een SEF-ADR, dan `kwh_prijs_nu` (`vergelijkingsdienst.py`, `vandaag_morgen` stopt nu op `maandgrens`)
en de route `/v1/mij/prijs`. Er komt een veld `raming` per kwartier bij. Oudere integraties negeren het. De
integratie toont `raming` in het attribuut van het venster. Een eigen SEF-release met deploy, die 0.18.0 niet
blokkeert.

## 0.19.0: Peak Guard

**Doel:** zien dat een nieuwe maandpiek dreigt vóór het kwartier om is, en wat die kost. Werkt zonder SEF
(behalve de drempel en het tarief, die gecachet blijven).

Zuivere module `piek.py`:

- `kwartierstand(telwerk_begin, telwerk_nu, begin, nu)`: het gemiddelde vermogen in kW tot nu, uit het
  verschil op de afnametelwerken (`afname_1` + `afname_2`), niet uit het integreren van vermogen.
- `verwachte_piek(gemiddeld, vermogen_nu, verstreken, kwartier)`: projectie tot het einde van het kwartier.
  Zonder vermogenssensor wordt het vermogen geschat uit het telwerkverschil van de laatste minuut.
- `drempel(maandpiek_kw, ondergrens_kw, doel_kw)`: max van de drie. `doel_kw` is optioneel.
- `kost_nieuwe_piek(boven_drempel_kw, tarief_eur_kw_jaar)`: eerst nagaan tegen de capaciteitsberekening van
  SEF (`capaciteit.maanden[].kost_eur`), dan pas een formule. Geen eigen tariefregel.

Integratie:

- De telwerkstand bij elke kwartiergrens bewaren. Na een herstart midden in een kwartier is dat kwartier
  `onbekend`. Geen gok.
  `# ponytail: eerste kwartier na herstart onbekend; begintoestand uit de recorder halen als dat stoort`
- Een optionele sleutel `vermogen` (W of kW, net) in de configuratie en in reconfigure.
- De maandpiek, de ondergrens en het tarief komen uit de `capaciteit` van `/mij/prijs`.
- Entiteiten:
  - `sensor.sef_kwartiervermogen` (gemiddelde tot nu);
  - `sensor.sef_verwachte_kwartierpiek`;
  - `sensor.sef_piekruimte` (kW tot de drempel);
  - `sensor.sef_kost_nieuwe_piek` (EUR);
  - `binary_sensor.sef_piekrisico`, met de attributen `drempel`, `bron_drempel` (maandpiek, ondergrens of
    doel) en `reden`.
- Een telwerk dat onbeschikbaar is → alle piekentiteiten onbeschikbaar, nooit `piekrisico = aan` op
  onbetrouwbare data.
- Geen acties: de gebruiker koppelt zelf een automatisering. Een voorbeeld komt in de README.

Tests in `test_piek.py` (nieuw):

- een constante last;
- een laststap halverwege het kwartier;
- de kwartiergrens;
- de overgang naar zomer- en wintertijd;
- een onbeschikbaar telwerk;
- een herstart midden in een kwartier;
- de ondergrens boven de maandpiek (begin van de maand).

## Daarna: beslissing ADR-0001, dan planner en modi

Eerst beslist Gert over ADR-0001 (lokaal of op de server plannen) en B5. Bij **lokaal plannen** (het voorstel):

1. `select.sef_modus` (monitor, advies, dry-run, automatisch). Standaard monitor. Automatisch enkel na een
   expliciete keuze. `switch.sef_pauze` met een duur.
2. `planner.py`, zuiver: de EV-lading (benodigde kWh, deadline, laadvermogen) over de goedkoopste kwartieren
   vóór de deadline, met de kost van een nieuwe piek uit `piek.py`. De deadline heeft altijd voorrang. Zonder
   prijzen: meteen laden tot de minimum-SOC.
3. Dry-run: elke beslissing als HA-event en in het logboek, met de reden en de gebruikte prijzen. Er wordt
   niets gestuurd.
4. Sturing enkel voor hardware die volgens B5 getest is. Merkspecifiek, dus eerst een opvolger van SEF
   ADR-0066 §8.

Bij **plannen op de server** komen er eerst een SEF-ADR (commandocontract, authenticatie, TTL), een threat
model en een DPIA voor EV-, aanwezigheids- en comfortdata.

## Per release

- Manifest `version` ophogen en een changelog in de README.
- Tests in `.venv-ha`: enkel de geraakte testbestanden.
- Eerst een dag in Gerts eigen HA, dan pas de release.
- Gert commit, pusht en maakt de release (`gh release create`).
- Daarna de website-zip opnieuw bouwen en `$versie` in `homeassistant.php` aanpassen.
