# SEF in Home Assistant

Custom component `sef` (ADR-0054 en ADR-0064 in de SEF-repo), MIT-licentie. Werkt enkel met een account op
[Energieplatform Vlaanderen](https://energieplatform-vlaanderen.be): alle cijfers komen van de rekenkern daar.
Doet vier dingen:

1. **Prijssensoren** `sensor.sef_afnameprijs` en `sensor.sef_injectieprijs`: wat één kWh nu kost (of
   opbrengt) met je huidige contract, all-in in EUR incl. btw, elk kwartier opgevraagd bij
   `GET /api/v1/mij/prijs`. De prijs per kwartier van vandaag en morgen staat in het attribuut `kwartieren`
   (bij een dynamisch contract morgen pas na 13 uur; nooit voorbij het einde van de maand).
   Uit dezelfde opvraging: `sensor.sef_contract_elektriciteit` (met gas ook `sensor.sef_contract_gas`, uit
   `/mij/gasprijs`): leverancier en product van je huidige contract, met de attributen `contracttype`,
   `vreg_id`, `prijsbron`, `contractdatum` en `tariefkaart_url` (de kaart die de prijs bepaalt; leeg als die
   niet gekend is, bv. bij een prijs uit het VREG-werkboek). Sinds 0.5.0.
2. **P1-upload**: twee minuten na elk kwartier leest hij de 5-minutenstatistieken van je vier
   P1-telwerken, maakt er kwartieren van en stuurt alles sinds de laatste meting in SEF naar
   `POST /api/v1/mij/aansluitingen/{id}/meetwaarden`. Een gemist kwartier (herstart) vult hij vanzelf aan,
   tot 10 dagen terug.
3. **Gas** (optioneel, sleutel `gas`): elk uur (7 minuten na het uur) het gasverbruik in m³ van elk volledig
   uur sinds de laatste gasmeting in SEF; SEF rekent om naar kWh met de omrekening uit je eigen
   Fluvius-gasexport. Plus de sensor `sensor.sef_gasprijs`: wat één m³ gas nu kost met je huidige
   gascontract (EUR/m³ incl. btw, de eenheid van je P1-gasmeter), elk uur opgevraagd bij
   `GET /api/v1/mij/gasprijs`.
   Het gastelwerk moet een statistiek hebben (Ontwikkelhulpmiddelen → Statistieken, eenheid m³). Zonder
   statistiek kan er niets geüpload worden; sinds 0.4.1 meldt de integratie dat in plaats van stil te stoppen
   (ook voor een elektriciteitstelwerk zonder statistiek, dat anders als 0 kWh telde).

4. **Waakhond** (`GET /api/v1/mij/waakhond`, elk uur):
   - `sensor.sef_besparing_elektriciteit` (EUR): wat het goedkoopste contract uit je laatste vergelijking
     minder kost dan je huidige contract, over de periode van die vergelijking (attributen: welk contract,
     de periode, wanneer vergeleken). Onbekend zonder vergelijking of zonder doorgerekend huidig contract;
     het attribuut `reden` zegt waarom. Negatief = je huidige contract is goedkoper.
   - `sensor.sef_contract_elektriciteit_dagen_tot_einde` (dagen): tot je lopende contract uit *Mijn
     contracten* eindigt; onbekend zonder einddatum.
   - Met gas (ingesteld, of een gascontract of -vergelijking in SEF) ook `…_gas`.

   De besparing is zo recent als je laatste vergelijking: start er een op de website om ze te verversen.

Download en uitleg ook op https://energieplatform-vlaanderen.be/homeassistant.php.

## Vooraf

- **Internet**: Home Assistant praat met `https://energieplatform-vlaanderen.be/api/v1` (sinds 0.2.0; geen Tailscale meer nodig).
  Controle (Terminal-add-on): `curl -s -o /dev/null -w '%{http_code}' https://energieplatform-vlaanderen.be/api/v1/status` geeft `401`
  (geen sleutel), niet een time-out.
- **Fluvius-export**: de P1-upload sluit aan op de laatste meting in SEF en kan hoogstens 10 dagen
  aanvullen. Staat je meting in SEF ouder, laad dan eerst een nieuwe Fluvius-export op de website.
- **Huidig contract** aangeduid op de website (voor de prijssensoren; voor de gasprijs ook je gascontract).
- **Gas**: een gasaansluiting met uurwaarden uit een Fluvius-export. Laad je recentste gasexport (opnieuw) op:
  daaruit haalt SEF de omrekening van m³ naar kWh (zonder die omrekening weigert SEF de gasupload met
  `GEEN_OMREKENFACTOR` en blijft de gasprijssensor onbekend). De P1-stroom staat per aansluiting aan of uit,
  dus zet hem ook voor je gasaansluiting aan.
- **P1-stroom aan** op de website: *Mijn Energieplatform → Instellingen → P1-meter → P1-stroom toestaan*.
  Zonder die toestemming weigert SEF elke upload (`P1_GEEN_TOESTEMMING`, melding in Home Assistant). Daar
  stop je de stroom ook en wis je de P1-gegevens.
- **Sleutel**: maak een **Home Assistant-sleutel** op *Mijn Energieplatform → API-sleutels* (de sleutel
  verschijnt één keer). Of, door de beheerder in een eigen terminal:
  ```bash
  python scripts/api_sleutel.py --maak --email <adres> --label "Home Assistant" --scopes dossier:lezen dossier:schrijven
  ```
  Zonder `dossier:schrijven` werken enkel de prijssensoren.

## Installeren

1. Kopieer de map `custom_components/sef/` naar `/config/custom_components/sef/` in Home Assistant
   (bv. met de add-on *Samba share* of *Studio Code Server*), of voeg deze repo in HACS toe als
   *custom repository* (type *Integration*). Herstart Home Assistant.
2. *Instellingen → Apparaten en diensten → Integratie toevoegen → Energieplatform Vlaanderen*. Eerst het adres (laat staan) en je
   Home Assistant-sleutel; SEF controleert hem meteen. Daarna optioneel de P1-meter (punten 3 en 4): laat
   alles leeg voor enkel de prijs- en waakhondsensoren. Later de meter of telwerken toevoegen of wijzigen:
   de integratie verwijderen en opnieuw toevoegen (een optiescherm is er nog niet; het meter-ID wijzig je best
   nooit, SEF weigert een ander ID).
3. Zoek de entiteiten van de HomeWizard P1: *Instellingen → Ontwikkelhulpmiddelen → Statussen*, zoek op
   `energy_import_tariff` en `energy_export_tariff`. Het zijn kWh-tellers (`state_class: total_increasing`).
4. Het meter-ID: het serienummer van de HomeWizard P1-meter (*Instellingen → Apparaten → HomeWizard →
   apparaatinfo*), enkel letters en cijfers. SEF koppelt de eerste upload aan dit ID; een ander ID daarna
   wordt geweigerd. Kies het dus één keer en laat het staan.
5. Klaar: na een minuut loopt de eerste upload. Meldingen van SEF verschijnen als melding in Home
   Assistant (*SEF weigerde de P1-upload …*) en in het logboek.

**Of in YAML** (zoals tot versie 0.3.0; wordt bij het opstarten in de integratie ingelezen, en een wijziging
in de YAML werkt die bij). In `secrets.yaml`: `sef_sleutel: sef_...`, en in `configuration.yaml`:
   ```yaml
   sef:
     sleutel: !secret sef_sleutel
     meter: 5c2fafabcdef            # serienummer van de P1-meter
     afname_1: sensor.p1_meter_energy_import_tariff_1
     afname_2: sensor.p1_meter_energy_import_tariff_2
     injectie_1: sensor.p1_meter_energy_export_tariff_1
     injectie_2: sensor.p1_meter_energy_export_tariff_2
     tarief_1: dag                  # Belgische afspraak: 1.8.1/2.8.1 = dag. Omgekeerd? Zet nacht.
     # enkelvoudig: true            # enkel bij een enkelvoudige meter
     gas: sensor.gas_meter_gas     # optioneel: gastelwerk in m³ (HomeWizard: apparaat "Gas meter")
   logger:
     logs:
       custom_components.sef: debug
   ```
   Zonder `meter` of telwerken: enkel de prijs- en waakhondsensoren. Wie van 0.3.0 komt, houdt dezelfde
   entiteiten (zelfde `unique_id`), dus ook het Energy-dashboard blijft werken (nagekeken met HA 2025.1.4:
   0.3.0 met YAML gestart, daarna 0.4.0, zelfde `entity_id`'s). De YAML vervangt bij elke start de ingave: een
   regel die je schrapt, verdwijnt ook daar.

## Een melding als het goedkoper kan

Voorbeeld van een automatisering (de besparing geldt voor de periode van je laatste vergelijking):

```yaml
automation:
  - alias: SEF - goedkoper contract
    trigger:
      - platform: numeric_state
        entity_id: sensor.sef_besparing_elektriciteit
        above: 50
    action:
      - service: notify.notify
        data:
          message: >
            {{ state_attr('sensor.sef_besparing_elektriciteit', 'beste_contract') }} kost
            {{ states('sensor.sef_besparing_elektriciteit') }} EUR minder dan je huidige contract.
  - alias: SEF - contract loopt af
    trigger:
      - platform: numeric_state
        entity_id: sensor.sef_contract_elektriciteit_dagen_tot_einde
        below: 31
    action:
      - service: notify.notify
        data:
          message: Je elektriciteitscontract loopt over {{ states('sensor.sef_contract_elektriciteit_dagen_tot_einde') }} dagen af.
```

## Energy-dashboard

*Instellingen → Dashboards → Energie → Elektriciteitsnet*: bij elk afnametelwerk "Gebruik een entiteit
met de huidige prijs" → `sensor.sef_afnameprijs`; bij elk injectietelwerk → `sensor.sef_injectieprijs`.
De vaste kosten (vaste vergoeding, capaciteitstarief) zitten bewust niet in die prijs.

*Gas*: bij het gastelwerk (`sensor.gas_meter_gas`) "Gebruik een entiteit met de huidige prijs" →
`sensor.sef_gasprijs`. Ook hier zonder vaste vergoeding en vaste netkost. De attributen tonen de prijs per
kWh en de gebruikte omrekening (kWh per m³ en de maand waarvan ze komt).

## Wat je moet weten

- **Herstart of storing**: Home Assistant zet het verbruik van een periode zonder statistiek in het eerste
  blok erna. Het totaal klopt, het tijdstip niet (dat kan een maandpiek te hoog laten lijken). Een latere
  Fluvius-export vervangt die kwartieren: Fluvius gaat altijd voor.
- **Eén register per kwartier**: het telwerk dat bewoog (tarief 1 of 2). Bij een wissel binnen het
  kwartier gaat alles naar het grootste.
- **Gas in m³, SEF rekent om**: met de omrekening van de laatste volledige maand in je Fluvius-export
  (de werkelijke maand wijkt doorgaans minder dan 1 % af). Een latere Fluvius-export vervangt de P1-uren
  door de juiste waarden.
- **Limiet**: SEF aanvaardt 12 uploads per uur per sleutel; normaal is het er 4 (met gas 5).

## Tests

De rekenlogica (`p1.py`) heeft geen Home Assistant nodig; de upload vraagt `homeassistant`, het
configuratiescherm en de sensoren `pytest-homeassistant-custom-component` (anders overgeslagen):

```bash
pip install "pytest-homeassistant-custom-component==0.13.205"   # hoort bij Home Assistant 2025.1.4
python -m pytest -q tests
```
