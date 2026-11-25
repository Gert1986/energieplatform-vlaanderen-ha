# Energieplatform Vlaanderen voor Home Assistant

Home Assistant-integratie (domein `sef`, MIT-licentie) van
[Energieplatform Vlaanderen](https://energieplatform-vlaanderen.be), het onafhankelijke platform dat elk
Vlaams energiecontract doorrekent op je eigen meetdata. Werkt enkel met een account daar: alle cijfers komen
van de rekenkern van het platform.

**Documentatie en uitleg:** <https://energieplatform-vlaanderen.be/homeassistant.php> ·
**Je account en sleutel:** <https://energieplatform-vlaanderen.be/mijn/homeassistant.php> ·
**Installeren:** via HACS (aangepaste repository, zie [Installeren](#installeren)), of rechtstreeks:

[![Openen in Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gert1986&repository=energieplatform-vlaanderen-ha&category=integration)

Doet vier dingen:

1. **Prijssensoren** `sensor.sef_afnameprijs` en `sensor.sef_injectieprijs`: wat één kWh nu kost (of
   opbrengt) met je huidige contract, all-in in EUR incl. btw, elk kwartier opgevraagd bij
   `GET /api/v1/mij/prijs`. De prijs per kwartier van vandaag en morgen staat in het attribuut `kwartieren`
   (bij een dynamisch contract morgen pas na 13 uur; nooit voorbij het einde van de maand).
   Uit dezelfde opvraging: `sensor.sef_contract_elektriciteit` (met gas ook `sensor.sef_contract_gas`, uit
   `/mij/gasprijs`): leverancier en product van je huidige contract, met de attributen `contracttype`,
   `vreg_id`, `prijsbron`, `contractdatum` en `tariefkaart_url` (de kaart die de prijs bepaalt; leeg als die
   niet gekend is, bv. bij een prijs uit het VREG-werkboek). Sinds 0.5.0.
   Ook uit dezelfde opvraging, sinds 0.12.0: `sensor.sef_spotprijs` (EUR/MWh, zonder btw of kosten): de
   day-ahead-prijs van de Belgische beurs voor het kwartier van nu, bij elk contract (bij een vast of
   variabel contract ter informatie), met de prijs per kwartier van vandaag en, na ±13 uur, morgen in het
   attribuut `kwartieren`. Geen eigen ENTSO-E-sleutel nodig. Bron: ENTSO-E Transparency Platform, met
   energy-charts.info (Fraunhofer ISE, CC BY 4.0) als terugval.
   Sinds 0.13.0 `sensor.sef_beursprijs_all_in` (EUR/kWh): diezelfde beursprijs, maar ingepakt zoals je eigen
   prijs, met dezelfde netkosten, heffingen en btw. Zet je die naast `sensor.sef_afnameprijs`, dan vergelijk
   je op dezelfde voet: het verschil is wat je leverancier bovenop de beurs rekent (marge, groene stroom,
   WKK, evenwicht). De kale spotprijs (EUR/MWh, zonder iets) is daarvoor niet geschikt. Vaste kosten zitten
   in geen van beide. Per kwartier in het attribuut `kwartieren`; zie [Grafiek](#grafiek-eigen-prijs-tegenover-de-beurs).

   Sinds 0.14.1 loopt `sensor.sef_afnameprijs` (en de injectieprijs) met de klok mee: ze toont de prijs van het
   lopende kwartier uit `kwartieren`. Daarvoor kon ze tot 15 minuten achterlopen, omdat ze de waarde van de
   laatste opvraging bleef tonen (die loopt per kwartier vanaf het opstarten van Home Assistant). Sinds 0.15.1
   schrijft ze die waarde ook echt weg op de kwartiergrens; in 0.14.1 en 0.15.0 rekende ze het juiste kwartier,
   maar zag Home Assistant het pas bij de volgende opvraging (zelfde achterstand). Geldt ook voor
   `sensor.sef_spotprijs` en `sensor.sef_beursprijs_all_in`.

   **Opbouw van de prijs** (sinds 0.14.0): het attribuut `opbouw` van `sensor.sef_afnameprijs` splitst de
   prijs van nu in `energie` (wat je leverancier rekent, na een eventuele kWh-korting), `net` (distributie en
   transmissie), `heffingen` en `btw`, in EUR/kWh; samen zijn ze de prijs van de sensor. Bij
   `sensor.sef_injectieprijs` splitst `opbouw` in `beurs` (de day-ahead-prijs van dit kwartier) en `marge`
   (wat je krijgt min de beurs; negatief is wat je leverancier inhoudt). Zo leg je de prijs post per post naast
   je eigen berekening. Vraagt een oudere SEF-server het veld niet mee, dan ontbreekt het attribuut gewoon.

   **Pieken en capaciteitstarief** (sinds 0.13.0, uit dezelfde opvraging; enkel met een digitale meter):
   - `sensor.sef_maandpiek` (kW): je hoogste kwartierafname van deze maand tot nu (met P1 elk kwartier
     bijgewerkt). Attributen: `kost_eur` (wat die piek aan capaciteitstarief kost, EUR incl. btw),
     `maanden` (maand, `piek_kw` en `kost_eur` van elke gemeten maand van het voorbije jaar),
     `ondergrens_kw` (2,5 kW: een lagere piek wordt als 2,5 kW aangerekend) en `tarief_eur_kw_jaar`.
   - `sensor.sef_hoogste_piek` (kW): de hoogste maandpiek van de laatste 12 maanden, met `maand` en `kost_eur`.
   - `sensor.sef_capaciteitstarief` (EUR): wat al die maandpieken samen kosten, met `gemiddelde_piek_kw`
     (het gemiddelde waarop Fluvius rekent, minstens 2,5 kW per maand) en `maanden` (hoeveel gemeten).
     Fluvius rekent elke maand het gemiddelde van je laatste 12 maandpieken aan, dus elke piek telt 12
     maanden lang voor 1/12 mee: `kost_eur` is wat die ene piek over die 12 facturen samen kost. Alles op het
     tarief van je netbeheerder van vandaag, zonder het maximumtarief en de wettelijke ondergrens van de
     netkost (die raken enkel heel kleine of heel grote verbruikers); een maand zonder meting telt niet mee.
2. **P1-upload**: twee minuten na elk kwartier leest hij de 5-minutenstatistieken van je vier
   P1-telwerken, maakt er kwartieren van en stuurt alles sinds de laatste meting in SEF naar
   `POST /api/v1/mij/aansluitingen/{id}/meetwaarden`. Een gemist kwartier (herstart) vult hij vanzelf aan,
   tot 10 dagen terug.

   **Thuisbatterij** (optioneel, sleutels `batterij_laden` en `batterij_ontladen`, sinds 0.10.0): vier
   minuten na elk kwartier ook hoeveel je batterij in dat kwartier laadde en ontlaadde, in eigen aanvragen
   (een batterijfout houdt de netkwartieren niet tegen). Daarmee meet SEF wat de batterij echt bespaart
   (*Mijn investeringen*) in plaats van het te schatten tegen vorig jaar. Je hebt twee **telwerken** nodig
   van de energie aan de wisselstroomkant (kWh of Wh, `state_class: total_increasing`), geen vermogen:
   - Marstek Venus via Modbus: de sensoren *Total charging energy* en *Total discharging energy*
     (de naam hangt af van de integratie; zoek in *Ontwikkelhulpmiddelen → Statistieken* op `charg`).
   - Heeft je batterij enkel een vermogen (W): maak er per richting een telwerk van met de helper
     *Integraal (Riemann-som)*, methode *links*, eenheid kWh, op een sensor die enkel laden (of enkel
     ontladen) positief toont.

   De eerste keer begint de upload bij het eerste volledige kwartier dat het telwerk in de statistiek heeft
   (hoogstens 10 dagen terug), daarna na het laatste batterijkwartier in SEF. Meer dan 10 kWh in één kwartier
   stopt de upload met een Repair: dan meldt het telwerk meestal Wh terwijl het kWh zegt.
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
   - `sensor.sef_gemiddelde_prijs_elektriciteit` (c€/kWh, sinds 0.11.0): wat een kWh met je huidige
     contract gemiddeld kost, alles inbegrepen (energie, vaste vergoeding, nettarief met capaciteitstarief,
     heffingen en btw) gedeeld door je afname over de periode van je laatste vergelijking. Injectie wordt
     niet verrekend, zodat zonnepanelen het getal niet drukken. Attributen: `beste_contract` en
     `beste_gemiddelde_prijs` (het goedkoopste contract uit die vergelijking), de periode en `reden` als
     het onbekend is. Dit is geen prijs voor het Energy-dashboard: daarvoor dient `sensor.sef_afnameprijs`
     (wat één kWh méér kost).
   - Met gas (ingesteld, of een gascontract of -vergelijking in SEF) ook `…_gas`.

   De besparing en de gemiddelde prijs zijn zo recent als je laatste vergelijking: start er een op de website om ze te verversen.

5. **Voorschot** (`GET /api/v1/mij/voorschot`, elk uur; sinds 0.6.0). Geef je maandvoorschot in op de
   website (*Mijn voorschot*); SEF rekent de kost met je huidige contract op je eigen meting:
   - `sensor.sef_kost_deze_maand_elektriciteit` (EUR): wat je verbruik deze maand al kostte, tot de laatste
     volledige meetdag. Attributen: `voorschot_maand`, `voorschot_tot_nu` (je voorschot voor dezelfde
     dagen, naar rato), `verschil` (positief = meer dan je voorschot), `vorige_maand_kost` en
     `vorige_maand_verschil`. Sinds 0.8.0 ook `laatste_14_dagen_kost` en
     `zelfde_dagen_vorig_jaar_kost` (dezelfde weekdagen 52 weken eerder, zelfde prijzen): zo zie je een
     recente besparing (batterij, toestel uit) al vóór de jaarinschatting ze oppikt.
   - `sensor.sef_jaarschatting_elektriciteit` (EUR): de kost van de laatste 12 volle maanden met je huidige
     contract. Attributen: `voorschot_jaar` (12 × je voorschot), `saldo` en `aangeraden_voorschot`. Onbekend
     met minder dan 12 volle maanden meetdata; `reden` zegt waarom.
   - `sensor.sef_voorschot_saldo_elektriciteit` (EUR): jaarschatting min 12 × je voorschot; positief =
     bijbetalen op de afrekening, negatief = terugkrijgen.
   - `sensor.sef_kost_sinds_afrekening_elektriciteit` (EUR, sinds 0.9.0): wat je verbruik kostte sinds je
     laatste afrekening, tot de laatste volledige meetdag. Geef de datum in op de website (*Mijn voorschot*,
     begin van de lopende afrekeningsperiode). Attributen: `van`, `tot` (exclusief), `voorschot` (je voorschot
     voor die periode, naar rato per maand), `verschil` (positief = meer dan je voorschot) en `reden`.
     Elektriciteit en gas samen: een template-sensor, bv.
     `{{ states('sensor.sef_kost_sinds_afrekening_elektriciteit') | float(0) + states('sensor.sef_kost_sinds_afrekening_gas') | float(0) }}`.
   - Met gas (ingesteld, of een gasaansluiting in SEF) ook `…_gas`. Sinds 0.7.0 heeft
     `sensor.sef_jaarschatting_gas` ook de kost bij normaal weer: `normaal_kost`, `normaal_saldo`,
     `graaddagen` (het gemeten jaar), `graaddagen_normaal` (gemiddelde van dezelfde maanden in de 10 vorige
     jaren, KMI-station van je provincie), `referentie`, `referentie_jaren` (volledige jaren die meetelden, minstens 8) en `normaal_reden`. Het aangeraden voorschot volgt
     voor gas dat normale jaar. `sensor.sef_voorschot_saldo_gas` krijgt het attribuut `normaal_saldo`.

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
  Zonder die toestemming weigert SEF elke upload (`P1_GEEN_TOESTEMMING`, Repair in Home Assistant). Daar
  stop je de stroom ook en wis je de P1-gegevens.
- **Batterijstroom aan** (enkel met de batterijsleutels): op dezelfde plaats, *Batterijstroom toestaan*.
  Een eigen toestemming; zonder weigert SEF de batterijkwartieren (`BATTERIJ_GEEN_TOESTEMMING`), de
  netkwartieren lopen gewoon door.
- **Sleutel**: maak een **Home Assistant-sleutel** op *Mijn Energieplatform → API-sleutels* (de sleutel
  verschijnt één keer). Of, door de beheerder in een eigen terminal:
  ```bash
  python scripts/api_sleutel.py --maak --email <adres> --label "Home Assistant" --scopes dossier:lezen dossier:schrijven
  ```
  Zonder `dossier:schrijven` werken enkel de prijssensoren.

## Installeren

1. **Met HACS:** de knop *Openen in Home Assistant* hierboven (ook op de website, na het aanmaken van je
   sleutel) vraagt eenmalig het adres van je Home Assistant en opent daar HACS met deze repository al
   ingevuld: *Downloaden*, herstart Home Assistant en ga verder bij punt 2. Met de hand:
   *HACS → ⋮ (drie puntjes) → Aangepaste repositories*, plak
   `https://github.com/Gert1986/energieplatform-vlaanderen-ha`, kies type *Integratie* en voeg toe. Zoek
   daarna *Energieplatform Vlaanderen* in HACS, download en herstart Home Assistant. Een nieuwe versie
   verschijnt dan vanzelf als update in HACS.
   **Zonder HACS:** kopieer de map `custom_components/sef/` naar `/config/custom_components/sef/` (bv. met de
   add-on *Samba share* of *Studio Code Server*), of pak de zip van
   <https://energieplatform-vlaanderen.be/homeassistant.php> daar uit. Herstart Home Assistant.
2. *Instellingen → Apparaten en diensten → Integratie toevoegen → Energieplatform Vlaanderen*. Eerst het adres (laat staan) en je
   Home Assistant-sleutel; SEF controleert hem meteen. Daarna optioneel de P1-meter (punten 3 en 4): laat
   alles leeg voor enkel de prijs- en waakhondsensoren. Later de meter, telwerken of sleutel wijzigen (sinds
   0.17.0): bij de integratie *⋮ → Opnieuw configureren*. Laat de sleutel leeg om de huidige te houden; maak
   een telwerkveld leeg om het weg te halen. De entiteiten blijven dezelfde. Het meter-ID wijzig je best
   nooit: SEF weigert een ander ID (dat verschijnt dan als Repair). Komt je ingave uit de YAML, pas dan de
   YAML aan.
3. Zoek de entiteiten van de HomeWizard P1: *Instellingen → Ontwikkelhulpmiddelen → Statussen*, zoek op
   `energy_import_tariff` en `energy_export_tariff`. Het zijn kWh-tellers (`state_class: total_increasing`).
4. Het meter-ID: het serienummer van de HomeWizard P1-meter (*Instellingen → Apparaten → HomeWizard →
   apparaatinfo*), enkel letters en cijfers. SEF koppelt de eerste upload aan dit ID; een ander ID daarna
   wordt geweigerd. Kies het dus één keer en laat het staan.
5. Klaar: na een minuut loopt de eerste upload. Weigert SEF een upload, of heeft een telwerk geen
   statistiek, dan verschijnt dat sinds 0.16.0 onder *Instellingen → Reparaties* (*SEF weigerde de
   P1-upload …*) en in het logboek; de Repair verdwijnt vanzelf zodra de upload weer lukt. Een tijdelijke
   fout (SEF onbereikbaar of bezet, te veel aanvragen) komt enkel in het logboek: het volgende venster
   probeert opnieuw. Tot 0.15.1 was elke weigering een melding.

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
     vermogen: sensor.p1_meter_power  # optioneel: netvermogen (W) voor Peak Guard, blijft lokaal
     # batterij_soc: sensor.marstek_venus_modbus_batterij_soc   # optioneel, batterijplanner: alle drie of geen
     # batterij_capaciteit: 5.12      # bruikbare kWh
     # batterij_vermogen: 2500        # maximaal laadvermogen in W
     # batterij_max_soc: 100          # optioneel: laden tot (de eigen grens van je batterij)
     gas: sensor.gas_meter_gas     # optioneel: gastelwerk in m³ (HomeWizard: apparaat "Gas meter")
     # batterij_laden: sensor.venus_total_charging_energy        # optioneel: beide of geen
     # batterij_ontladen: sensor.venus_total_discharging_energy
   logger:
     logs:
       custom_components.sef: debug
   ```
   Zonder `meter` of telwerken: enkel de prijs- en waakhondsensoren. Wie van 0.3.0 komt, houdt dezelfde
   entiteiten (zelfde `unique_id`), dus ook het Energy-dashboard blijft werken (nagekeken met HA 2025.1.4:
   0.3.0 met YAML gestart, daarna 0.4.0, zelfde `entity_id`'s). De YAML vervangt bij elke start de ingave: een
   regel die je schrapt, verdwijnt ook daar.

## Updates

Een nieuwe versie verschijnt in HACS als update. Wat je daarbij mag verwachten:

- **Entity-ID's blijven.** Geen enkele bestaande sensor krijgt een andere `unique_id`, dus je dashboards,
  automatiseringen en het Energy-dashboard blijven werken. Een naam of ID die je zelf wijzigde, blijft.
- **Nieuwe sensoren komen er enkel bij.** Wat er was, verdwijnt niet zonder vermelding in de release notes.
- **In elke taal dezelfde ID's.** De namen liggen vast in de integratie en hangen niet af van de taal van je
  Home Assistant: ook in een Engelse of Franse installatie heet het `sensor.sef_afnameprijs`.
- **Geen verzonnen nul.** Ontbreekt een waarde (bv. de prijs van morgen vóór 13 uur, gas zonder omrekening),
  dan is de sensor *onbekend*; is SEF niet te bereiken, dan *niet beschikbaar*. Nooit 0 alsof dat klopt.

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

## De vaatwasser in de goedkoopste uren

Sinds 0.18.0 rekent de integratie dat zelf uit, lokaal uit de prijzen die ze al heeft:

| Entiteit | Wat |
|---|---|
| `sensor.sef_goedkoopste_1u`, `_2u`, `_4u` | Begin van het goedkoopste aaneengesloten blok van 1, 2 of 4 uur in de gekende prijzen; attributen `einde` en `gemiddelde_prijs`. Eens begonnen blijft het staan tot het voorbij is. *Onbekend* als er minder prijzen gekend zijn dan het blok lang is. |
| `sensor.sef_prijsrang` | 0 tot 100: hoeveel procent van de gekende kwartieren vanaf nu goedkoper is. 0 = nu is het goedkoopst. Attributen `horizon_tot` (tot wanneer de prijzen gekend zijn) en `prijsniveaus`. |
| `binary_sensor.sef_goedkoop_nu` | Aan bij een prijsrang van hoogstens 25. |
| `binary_sensor.sef_duur_nu` | Aan bij een prijsrang van minstens 75. |

Werkt bij elk contract: dynamisch volgt het de beurs, dag/nacht kiest de nacht (alle nachtkwartieren hebben
rang 0, `prijsniveaus` is dan 2). De prijzen van morgen komen er rond 13 uur bij; tot dan kijkt alles enkel
naar vandaag. SEF geeft geen prijzen voorbij het einde van de maand: op de laatste dag stopt de horizon om
middernacht.

```yaml
automation:
  - alias: SEF - vaatwasser in de goedkoopste 2 uur
    trigger:
      - platform: time
        at: sensor.sef_goedkoopste_2u
    condition:   # zelf aan te maken helper: aan = gevuld en klaar om te starten
      - condition: state
        entity_id: input_boolean.vaatwasser_klaar
        state: "on"
    action:
      - service: switch.turn_on
        target: {entity_id: switch.vaatwasser}
      - service: input_boolean.turn_off
        target: {entity_id: input_boolean.vaatwasser_klaar}
```

Een kortere of langere cyclus: `sensor.sef_goedkoopste_1u` of `_4u`. Zet je `vaatwasser_klaar` pas aan als
het blok al begonnen is, dan is het tijdstip voorbij en wacht de vaatwasser op het volgende blok (misschien
morgen). Na een herstart van Home Assistant midden in een blok toont de sensor het volgende blok.

## Energy-dashboard

*Instellingen → Dashboards → Energie*, bij elk telwerk "Gebruik een entiteit met de huidige prijs":

| Vak in het Energy-dashboard | Telwerk (HomeWizard) | Prijsentiteit |
|---|---|---|
| Elektriciteitsnet → Netverbruik | `…_energy_import_tariff_1` en `_2` | `sensor.sef_afnameprijs` |
| Elektriciteitsnet → Teruglevering aan het net | `…_energy_export_tariff_1` en `_2` | `sensor.sef_injectieprijs` |
| Gasverbruik | `sensor.gas_meter_gas` | `sensor.sef_gasprijs` |

De vaste kosten (vaste vergoeding, capaciteitstarief, vaste netkost gas) zitten bewust niet in die prijzen. De attributen tonen de prijs per
kWh en de gebruikte omrekening (kWh per m³ en de maand waarvan ze komt).

## Grafiek: eigen prijs tegenover de beurs

Met [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (HACS) uit de attributen `kwartieren`,
beide in EUR/kWh op één as:

```yaml
type: custom:apexcharts-card
header: {show: true, title: Wat u betaalt tegenover de beurs}
graph_span: 1d
span: {start: day}
now: {show: true}
yaxis: [{decimals: 2}]
series:
  - entity: sensor.sef_afnameprijs
    name: Mijn prijs (all-in)
    type: column
    data_generator: return entity.attributes.kwartieren.map(k => [new Date(k.start).getTime(), k.prijs]);
  - entity: sensor.sef_beursprijs_all_in
    name: Beurs (zelfde netkosten en btw)
    type: line
    data_generator: return entity.attributes.kwartieren.map(k => [new Date(k.start).getTime(), k.prijs]);
```

## Peak Guard: een nieuwe maandpiek zien aankomen

Sinds 0.19.0, met de afnametelwerken (`afname_1`, `afname_2`). Elke minuut, lokaal: enkel de drempel en het
capaciteitstarief komen uit SEF, en die blijven staan als SEF even onbereikbaar is. Peak Guard stuurt niets:
het geeft signalen, de automatisering kies je zelf.

| Entiteit | Wat |
|---|---|
| `sensor.sef_kwartiervermogen` | Gemiddelde afname van het lopende kwartier tot nu (kW), uit de telwerken. |
| `sensor.sef_verwachte_kwartierpiek` | Het kwartiergemiddelde als het vermogen van nu aanhoudt tot het einde van het kwartier. |
| `sensor.sef_piekruimte` | kW kwartiergemiddelde tot de drempel; negatief = erover. Geen ogenblikkelijk vermogen: met nog 5 van de 15 minuten te gaan is 1 kW ruimte zowat 3 kW extra voor de rest van het kwartier. |
| `sensor.sef_kost_nieuwe_piek` | Wat de verwachte piek deze maand extra kost aan capaciteitstarief (EUR incl. btw); 0 onder de drempel. |
| `binary_sensor.sef_piekrisico` | Aan als de verwachte piek boven de drempel uitkomt. Attributen `drempel`, `bron_drempel` (`maandpiek`, `lokaal`: een piek die SEF nog niet kent, of `ondergrens`) en `reden`. |

- Geef bij de meter ook het **netvermogen** op (`vermogen`, bv. `sensor.p1_meter_power` van HomeWizard, W of kW).
  Zonder rekent de integratie het vermogen uit het telwerk van de laatste minuut. Injectie telt als 0.
- Na een herstart van Home Assistant is het lopende kwartier *onbekend*: de stand bij het begin ontbreekt. Ook
  de lokaal geziene maandpiek (`bron_drempel: lokaal`) is dan vergeten; de maandpiek uit SEF blijft.
- Is een telwerk onbeschikbaar, dan zijn alle piekentiteiten *niet beschikbaar*, nooit "piekrisico aan".
- In de eerste minuten van een kwartier springt de verwachting mee met elk toestel dat aan- of uitgaat
  (een waterkoker). Wacht in een automatisering daarom best een minuut (`for:`).

```yaml
automation:
  - alias: SEF - laadpaal pauzeren bij piekrisico
    trigger:
      - platform: state
        entity_id: binary_sensor.sef_piekrisico
        to: "on"
        for: {minutes: 1}
    condition:
      - condition: state
        entity_id: switch.laadpaal
        state: "on"
    action:
      - service: switch.turn_off
        target: {entity_id: switch.laadpaal}
      - service: input_boolean.turn_on   # zelf aan te maken helper: enkel hervatten wat SEF pauzeerde
        target: {entity_id: input_boolean.laadpaal_gepauzeerd_door_sef}
  - alias: SEF - laadpaal hervatten na het kwartier
    trigger:
      - platform: state
        entity_id: binary_sensor.sef_piekrisico
        from: "on"
        to: "off"
        for: {minutes: 2}
    condition:
      - condition: state
        entity_id: input_boolean.laadpaal_gepauzeerd_door_sef
        state: "on"
    action:
      - service: switch.turn_on
        target: {entity_id: switch.laadpaal}
      - service: input_boolean.turn_off
        target: {entity_id: input_boolean.laadpaal_gepauzeerd_door_sef}
```

## Batterijplanner (proefdraai)

Sinds 0.20.0. De integratie rekent lokaal uit wanneer je thuisbatterij beter van het net laadt, en zegt dat in
`sensor.sef_beslissing`. **Ze stuurt niets**: dit is een proefdraai om na te kijken of de beslissingen kloppen.
Automatisch sturen komt pas later, en enkel voor een batterij die getest is.

Instellen bij de meter (*Opnieuw configureren*): de laadtoestand (%-sensor van je batterij), de bruikbare
capaciteit (kWh) en het maximale laadvermogen (W), en eventueel tot hoeveel % ze laadt.

| Entiteit | Wat |
|---|---|
| `select.sef_modus` | `monitor` (standaard): niets plannen. `proefdraai`: plannen en tonen. Blijft na een herstart. |
| `sensor.sef_beslissing` | Kort wat hij nu zou doen, bv. *Zou laden: goedkoop kwartier* of *Zelfverbruik: later goedkoper*. Attributen: `vermogen_w`, `detail` (prijzen en tijden). |

Hoe hij beslist, per kwartier:

- **Zou laden** als dit kwartier bij de goedkoopste hoort die nodig zijn om de batterij te vullen, en de
  prijs na 15 % verlies nog onder het gemiddelde van de duurste kwartieren erna ligt.
- Het laadvermogen blijft binnen de **piekruimte** van Peak Guard: laden maakt geen nieuwe maandpiek. Zonder
  afnametelwerk of na een herstart is die ruimte onbekend, en dan zegt hij nooit laden.
- **Zon:** wat je gisteren injecteerde, houdt hij vrij voor zonne-overschot. Dat is een ruwe schatting: op
  een zonnige dag na een grijze kan hij te veel van het net laden. Kijk dat in de proefdraai na.
- Anders **zelfverbruik**: de batterij doet wat ze zelf doet (bv. nul op de meter).

Een logboek op je dashboard (*Kaart toevoegen → Logboek*), beknopt: een regel per wissel, met het tijdstip.

```yaml
type: logbook
title: SEF
hours_to_show: 24
entities:
  - sensor.sef_beslissing
  - select.sef_modus
  - binary_sensor.sef_piekrisico
```

Elke wissel is ook het event `sef_beslissing` (met `modus`, `actie`, `reden` en `detail`) voor je eigen
automatiseringen.

## Wat je moet weten

- **Herstart of storing**: Home Assistant zet het verbruik van een periode zonder statistiek in het eerste
  blok erna. Het totaal klopt, het tijdstip niet (dat kan een maandpiek te hoog laten lijken). Een latere
  Fluvius-export vervangt die kwartieren: Fluvius gaat altijd voor.
- **Eén register per kwartier**: het telwerk dat bewoog (tarief 1 of 2). Bij een wissel binnen het
  kwartier gaat alles naar het grootste.
- **Gas in m³, SEF rekent om**: met de omrekening van de laatste volledige maand in je Fluvius-export
  (de werkelijke maand wijkt doorgaans minder dan 1 % af). Een latere Fluvius-export vervangt de P1-uren
  door de juiste waarden.
- **Een probleem melden**: *Instellingen → Apparaten en diensten → Energieplatform Vlaanderen → ⋮ →
  Diagnostiek downloaden* (sinds 0.15.0) en voeg het bestand bij je issue. Je sleutel, meter-ID, bedragen en
  contract staan er niet in, en sinds 0.16.0 ook geen eigen adres van SEF en geen entity-ID's van je
  telwerken: enkel je instellingen, de versies van de integratie en Home Assistant, per opvraging bij SEF of
  ze lukte en welke velden terugkwamen, en per upload wanneer hij laatst liep en lukte, met de foutcode.
- **Limiet**: SEF aanvaardt 12 uploads per uur per sleutel; normaal is het er 4 (met gas 5, met een
  batterij 4 meer).

## Tests

De rekenlogica (`p1.py`) heeft geen Home Assistant nodig; de upload vraagt `homeassistant`, het
configuratiescherm en de sensoren `pytest-homeassistant-custom-component` (anders overgeslagen):

```bash
pip install "pytest-homeassistant-custom-component==0.13.205"   # hoort bij Home Assistant 2025.1.4
python -m pytest -q tests
```
