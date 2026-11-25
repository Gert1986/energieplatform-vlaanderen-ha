# ADR-0001: Van prijsintegratie naar EMS, met lokaal plannen

## Status

VOORGESTELD (2026-11-24, concept: review van de EMS-roadmap, nog niet beslist door Gert)

## Context

De roadmap [EMS-ontwikkelroadmap v1.0](../ems-roadmap-v1.0.txt) (24 november 2026) wil de integratie laten
groeien van meten en prijzen (0.15.1) naar een energiemanagementsysteem: meten, voorspellen, plannen, sturen
en meten wat het opleverde. De roadmap is bewust ambitieus. Hij moet de integratie in de Home Assistant-wereld
laten opvallen.

In de roadmap is SEF "de economische reken- en planningslaag" en HA "de lokale uitvoeringslaag". SEF berekent
dus plannen, stuurt tijdelijke commando's (§8.1) en krijgt planningssamenvattingen terug (fase 7.7). Dat raakt
harde grenzen van SEF:

- **Privacy (SEF ADR-0052).** De vertrektijd van de EV, de SOC, of de EV thuis is (een aanwezigheidsprofiel),
  comforttemperaturen en PV- en verbruiksvoorspellingen zouden naar de server gaan. Dat is gedragsprofilering:
  het vraagt een DPIA, een doel en een bewaartermijn. Het testplan van de roadmap (§12) dekt enkel de
  diagnostics.
- **Vertrouwensgrens.** Commando's van de server naar HA zijn een nieuwe trust boundary. Daarvoor zijn een
  threat model, authenticatie (§8.1 noemt "gesigneerde opdrachten"), TTL, een watchdog en idempotentie nodig.
- **Merkonafhankelijkheid (SEF ADR-0066 §8).** "SEF en de HA-integratie kennen geen merk." Een batterij
  aansturen vraagt schrijfsemantiek per merk (fase 4).
- **Onafhankelijkheid.** Installateursprofielen (P4) kunnen een commerciële relatie worden.

Wat lokaal mogelijk is: `/mij/prijs` levert nu al de prijs per kwartier van vandaag en morgen (`afname`,
`injectie`, `spot_eur_mwh`, `beurs_allin`) en `capaciteit` (`tarief_eur_kw_jaar`, `ondergrens_kw` en de
maandpieken). Dat is alle economische invoer die een planner nodig heeft. Een EV-planner is "de goedkoopste
kwartieren vóór de deadline, met de kost van een nieuwe maandpiek erbij".

## Opties

1. **Roadmap zoals hij is.** SEF plant, HA voert uit wat SEF opdraagt.
2. **Lokaal plannen.** SEF levert prijzen en capaciteitstarief (zoals nu). De planner, Peak Guard en de
   sturing draaien in de integratie. Gedrags- en apparaatdata blijven in het huis. Enkel wat SEF al krijgt
   (P1- en batterijkwartieren) gaat nog naar de server.
3. **Enkel signalen.** Prijsrang, goedkope vensters en piekrisico als sensoren. De gebruiker stuurt zelf met
   automatiseringen. Geen planner.

## Beslissing

Nog open. Voorstel: **optie 2**, ingevoerd langs optie 3. Eerst de signalen (fase 1 en 2), daarna de planner
in de integratie, en sturing pas na een dry-run op Gerts eigen opstelling.

## Reden

- Lokale controle is wat de HA-gemeenschap waardeert ("local first"). Een EMS dat zonder cloud plant, maakt
  meer indruk dan een EMS dat op een server wacht. Het klopt ook met de roadmap zelf (§4.1, §15: "geen realtime
  cloudafhankelijk peak shaving").
- Geen nieuwe persoonsgegevens op de server, dus geen DPIA voor profilering en geen bewaartermijnen.
- Het grootste deel van fase 3 valt weg: geen commandocontract van de server, geen handtekeningen, geen
  watchdog voor serveruitval en geen idempotentie op `command_id`. Lokale harde grenzen, modi
  (monitor/advies/dry-run/automatisch) en de manuele override blijven wel nodig.
- SEF blijft een vergelijkingsplatform en wordt geen partij die apparaten van anderen bedient.

## Bevindingen uit de review (2026-11-24)

### Al gebouwd, maar de roadmap behandelt het als nieuw

- **Diagnostics (§7.2)** bestaat sinds 0.15.0: `sleutel` en `meter` worden gemaskeerd en
  `test_diagnose_zonder_geheimen_of_bedragen` controleert de sleutel, de meter-ID, bedragen en de leverancier.
  Wat ontbreekt:
  - de tijdstippen van de laatste P1-, gas- en batterij-upload (de uploads zijn geen coordinators en staan
    dus niet onder `opvragingen`);
  - de versie van HA;
  - het maskeren van een `url` die afwijkt van de standaard (een tailnet-hostnaam kan naar de persoon leiden);
  - een momentopname van de volledige payload in plaats van gekende strings.
- **Fase 1** heeft geen serverwijziging nodig: alles staat in `kwartieren` en kan zuiver in `prijs.py`.
- **Gemeten besparing (§5.10, fase 9)** bestaat voor de batterij in SEF ADR-0066 (§2, §5, §7). Bouw daarop
  verder.
- **"Release 0.15.x"**: 0.15.1 bestaat al, dus het wordt 0.16.

### Eerst beslissen

- **YAML tegenover de options flow.** `async_step_import` overschrijft `entry.data` bij elke herstart als de
  YAML afwijkt. Een options flow wordt daardoor stil teruggezet. Beslis eerst wat voorrang krijgt (YAML maakt
  de UI read-only, of de YAML geldt enkel bij de eerste import). Kijk ook naar de `reconfigure`-stap van HA voor
  `url`, `sleutel` en de telwerken: dat is configuratie, geen optie.
- **Maandgrens.** `/mij/prijs` gaat nooit voorbij het einde van de lopende maand (SEF ADR-0054 §8). De laatste
  avond van een maand is er dus geen "morgen" voor het nachtvenster en de EV-planner. Mogelijke uitweg: de
  spotprijs met de formule van het huidige contract, gelabeld als raming. Dat is een beslissing aan de
  serverkant.

### Ontwerpgaten

- **Niet-dynamische contracten.** Daar wisselt de prijs enkel tussen dag en nacht. Een rang van 0 tot 100 is
  dan misleidend. Leg vast wat `cheap_now` voor hen betekent (de nachtzone?).
- **Capaciteitstarief in de doelfunctie.** Tot max(maandpiek tot nu, `ondergrens_kw`) is extra vermogen
  gratis. Daarboven kost elke kW een berekenbaar bedrag (telt 12 maanden mee in het gemiddelde). Peak Guard en
  de planner moeten die kost meenemen. 's Nachts laden kan een nieuwe piek maken (SEF ADR-0066 §5).
- **Kwartieraccumulator.** Gebruik het verschil op het P1-telwerk sinds het begin van het kwartier en
  integreer niet over vermogen. Dat overleeft een herstart van HA midden in een kwartier en gebruikt dezelfde
  bron als de upload.
- **Zomer- en wintertijd.** "4 uur" is 16 opeenvolgende kwartieren in absolute tijd, niet in kloktijd.
- **Wat het plan opleverde.** De reconstructie van ADR-0066 meet de batterij tegenover geen batterij. Het plan
  tegenover gewone zelfconsumptie is altijd een model en moet zo gelabeld worden.
- **Repairs (§7.3).** `_taak` maakt nu van elke `SefFout`, ook 5xx en 429, een blijvende melding. Filter op
  status en code. Neem ook de codes van de client zelf op: `GEEN_AANSLUITING` en `AANSLUITING`.
- **Namen.** De roadmap gebruikt Engels (`sef_cheapest_1h`), de bestaande entiteiten zijn Nederlands
  (`sef_afnameprijs`, `sef_maandpiek`). Kies één taal voor automatiseringen ervan afhangen.

### Omvang

- Opgeteld 36 tot 49 weken, voor één ontwikkelaar met een voltijdse job.
- Energy Score v1 is de prijsrang onder een andere naam: pas toevoegen als v2 echt extra invoer meeneemt.
- Sturing pas op hardware die Gert zelf kan testen. Open vraag: heeft zijn batterij een lokale HA-integratie
  waar je **naar kunt schrijven** (modus en vermogen), met terugmelding?

### Voorgestelde volgorde

1. Repairs, met een filter op tijdelijke fouten.
2. Een beslissing over de voorrang tussen YAML en de UI, daarna reconfigure of een options flow.
3. Fase 1 in `prijs.py`, met een beslissing over de maandgrens.
4. Peak Guard op het telwerk, met de capaciteitskost.
5. Deze ADR beslissen vóór fase 3.

## Gevolgen

### Positief

- Geen nieuwe persoonsgegevens op de server en geen nieuwe vertrouwensgrens.
- Werkt zonder internet zodra de prijzen van vandaag en morgen binnen zijn.
- Kleinere fase 3.

### Negatief

- De planner draait in de integratie, dus logica in Python aan de kant van HA in plaats van in de rekenkern
  van SEF.
- Een gemeten besparing per plan kan SEF enkel berekenen als het plan gekend is. Bij lokaal plannen blijft
  dat een lokaal rapport of een expliciete, opt-in upload (aparte beslissing en DPIA).

### Risico's

- Twee rekenplaatsen (SEF voor prijzen, HA voor de planning) die uit elkaar kunnen lopen. Beperken door de
  planner enkel met de prijzen van SEF te laten rekenen, nooit met een eigen prijsberekening.

## Relaties

- Documenten: [EMS-roadmap v1.0](../ems-roadmap-v1.0.txt)
- SEF: ADR-0052 (privacy en onafhankelijkheid), ADR-0054 (P1 via HA, §8 prijzen tot het einde van de maand),
  ADR-0064 (gas), ADR-0066 (gemeten batterijbesparing, §8 merkonafhankelijk)
- Vervangt: —
- Vervangen door: —
