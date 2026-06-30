# Manuale Utente — Teslatron Control

Questo manuale è rivolto a chi usa il software di controllo del Teslatron per la
prima volta. Descrive come avviare il programma, come orientarsi nell'interfaccia
grafica e come usare ogni funzione, incluso il sistema di ricette e i bottoni dei
template.

---

## Indice

1. [Cos'è questo software](#1-cosè-questo-software)
2. [Avvio rapido](#2-avvio-rapido)
3. [L'interfaccia grafica](#3-linterfaccia-grafica)
4. [Tab Overview](#4-tab-overview)
5. [Tab ITC](#5-tab-itc)
6. [Tab IPS](#6-tab-ips)
7. [Tab Plots](#7-tab-plots)
8. [Tab Commands](#8-tab-commands)
9. [Tab Recipes — Guida completa](#9-tab-recipes--guida-completa)
10. [Tab External Measurements](#10-tab-external-measurements)
11. [Tab Config](#11-tab-config)
12. [Log Viewer (strumento offline)](#12-log-viewer-strumento-offline)
13. [Regole di sicurezza](#13-regole-di-sicurezza)
14. [Risoluzione dei problemi comuni](#14-risoluzione-dei-problemi-comuni)
15. [Glossario](#15-glossario)

---

## 1. Cos'è questo software

Il Teslatron Control è un'applicazione web che gira localmente sul PC di laboratorio
e permette di controllare il criostato Teslatron del laboratorio Q-MAT (CNR-SPIN,
Università di Napoli Federico II).

Il software si apre nel **browser** (Chrome, Firefox, Edge). Non è un programma
Windows tradizionale con un'icona sul desktop: è un server che risponde su
`http://127.0.0.1:876X/`.

Cosa controlla:

| Grandezza | Strumento |
|-----------|-----------|
| Temperatura campione e VTI | Mercury iTC |
| Campo magnetico | Mercury iPS |
| Pressione gas / ago VTI | Mercury iTC (gas board) |
| Misure elettriche esterne | LabVIEW (coordinato via HTTP) |

---

## 2. Avvio rapido

### Prerequisiti (solo la prima volta)

```powershell
pip install -r requirements-service.txt
pip install pyvisa
python -m pip install --user -e .
```

### Avvio normale

Apri un terminale PowerShell nella cartella del progetto e usa uno dei comandi:

| Comando | Cosa fa | Porta |
|---------|---------|-------|
| `teslatron` | Modalità mock sicura (nessun hardware) | 8765 |
| `teslatron readonly` | Lettura dati dal lab reale | 8765 |
| `teslatron control` | Controllo completo del lab | 8766 |
| `teslatron heliox` | Sessione con sonda Heliox | 8767 |
| `teslatron --open-browser` | Avvia e apre il browser automaticamente | 8765 |

Dopo l'avvio, apri il browser e vai all'indirizzo stampato nel terminale,
tipicamente `http://127.0.0.1:8765/`.

> **Primo approccio?** Usa sempre `teslatron` (mock) per familiarizzare con
> l'interfaccia senza rischi. Nessun hardware viene toccato.

### Arresto

- **Nel terminale** dove hai lanciato il servizio: premi `Ctrl+C`.
- **Dalla GUI**: tab Commands → pulsante **Shutdown service**.
- **Da qualsiasi terminale**: `teslatron-stop`.

Chiudere il tab del browser **non ferma** il servizio.

---

## 3. L'interfaccia grafica

### Barra di stato in alto

La barra superiore mostra sempre lo stato globale del sistema:

```
[Service: Connected] [Backend: mock] [Mode: IDLE] [Safety: ok] [Access: Writable]
```

| Indicatore | Significato |
|-----------|-------------|
| **Service** | `Connected` = comunicazione OK con il backend; `Error` = problema |
| **Backend** | `mock` (simulazione), `standard` (Mercury reale), `heliox` (sonda Heliox) |
| **Mode** | `IDLE`, `RAMP_T`, `RAMP_B`, `HOLD`, `RECIPE`, `ERROR` |
| **Safety** | `ok`, `warning`, `critical` — limiti software attivi |
| **Access** | `READ ONLY` = solo lettura; `Writable` = comandi abilitati |

Se **Access** mostra `READ ONLY`, i pulsanti di comando sono disabilitati per
sicurezza. Devi riavviare con `teslatron control` per inviare comandi.

### Navigazione tra i tab

I tab principali sono visibili sotto la barra di stato:

```
Overview | ITC | IPS | Plots | Commands | Recipes | External | Config
```

Clicca su un tab per passare a quella sezione. La sezione attiva è evidenziata.

---

## 4. Tab Overview

Pannello di monitoraggio in tempo reale. Si aggiorna automaticamente ogni pochi
secondi via WebSocket (non serve ricaricare la pagina).

Mostra:

- **Temperature**: campione (Sample), VTI, magnete (Magnet), PT1, PT2
- **Field**: campo magnetico in Tesla, corrente e voltaggio IPS
- **Pressure**: pressione gas VTI in mbar
- **Trend charts**: grafici degli ultimi 30 minuti per le grandezze principali

È la vista giusta da tenere aperta durante un'acquisizione.

---

## 5. Tab ITC

Dettagli del Mercury iTC (controllore di temperatura).

Mostra i due **loop di temperatura** in parallelo:

### Sample loop (sonda campione)

- Temperatura attuale letta dal sensore
- Temperatura target impostata
- Potenza del riscaldatore (heater power %)
- Modalità PID (auto/manuale)
- Stato: `RAMP`, `STABLE`, `HOLD`

### VTI loop (Variable Temperature Insert)

- Temperatura attuale del VTI
- Controllo analogo al loop campione
- Gestione ago di gas e pressione

> I valori in questo tab sono in sola lettura. Per inviare comandi vai al tab
> **Commands**.

---

## 6. Tab IPS

Dettagli del Mercury iPS (alimentatore del magnete superconduttore).

Mostra:

- **Field**: campo magnetico attuale in Tesla
- **Current**: corrente nel magnete in Ampere
- **Voltage**: voltaggio di alimentazione
- **Switch heater**: stato dell'heater del persistent switch (On/Off)
- **Magnet temperature**: temperatura del criostato del magnete
- **PT temperatures**: PT1, PT2 (punti di riferimento criogenici)

> Il **switch heater** è critico: deve essere **On** prima di rampe di campo e
> **Off** per il funzionamento in modalità persistente. Non cambiarlo a mano se
> non sai cosa stai facendo.

---

## 7. Tab Plots

Grafici interattivi personalizzabili.

Puoi scegliere:

- **Quali grandezze visualizzare** (temperatura campione, VTI, campo, pressione, ...)
- **Finestra temporale**: da 5 minuti fino a 24 ore
- **Zoom e pan**: usa la rotella del mouse o trascina sull'asse X

I dati vengono letti dai log CSV salvati nella cartella `data/`. Se il log è
appena iniziato, il grafico mostrerà poco storico.

---

## 8. Tab Commands

Qui si inviano i comandi al criostato. Disponibile solo se **Access: Writable**.

### Ramp Temperature

Imposta una rampa di temperatura:

| Campo | Descrizione |
|-------|-------------|
| Loop | `sample`, `vti`, o `both` (VTI si porta al 90% della target) |
| Target K | Temperatura finale desiderata in Kelvin |
| Rate K/min | Velocità della rampa (es. `1.0` K/min) |
| Tolerance K | Banda di stabilità (es. `0.05` K) |
| Stable s | Secondi da mantenere dentro la banda prima di dichiarare "stabile" |

Clicca **Ramp** per avviare. Il **Mode** in alto diventerà `RAMP_T`.

### Set Temperature Target

Imposta un set point senza specificare la velocità (usa la rampa interna del
Mercury iTC).

### Ramp Field

Imposta una rampa di campo magnetico:

| Campo | Descrizione |
|-------|-------------|
| Target T | Campo finale in Tesla |
| Rate T/min | Velocità della rampa |
| Tolerance T | Banda di stabilità |

### Field to Zero

Porta il campo a zero in modo sicuro alla velocità impostata.

### Hold B / Hold T

`Hold B` interrompe la rampa di campo e mantiene il campo attuale.

`Hold T` interrompe la rampa di temperatura del loop selezionato e mantiene il
setpoint attuale.

### Abort

Interrompe immediatamente qualsiasi operazione in corso (rampa, ricetta, ecc.).
Più drastico di Hold.

### VTI Gas Controls

- **Set Needle**: imposta manualmente la posizione dell'ago (0–100%)
- **Set Pressure**: imposta la pressione target del gas VTI in mbar

### Switch Heater

Accende/spegne l'heater del persistent switch dell'iPS. Usare solo seguendo la
procedura corretta (vedi `LAB_RUNBOOK.md`).

### Shutdown Service

Spegne il backend Python in modo pulito.

---

## 9. Tab Recipes — Guida completa

Il sistema di **Recipes** (ricette) permette di programmare sequenze automatiche
di operazioni da eseguire in cascata senza presidiare il PC.

Una ricetta è una lista ordinata di **step**. Ogni step è un'operazione (rampa di
temperatura, attesa, misura, ecc.). Il backend le esegue nell'ordine, passando
allo step successivo solo quando quello corrente è completato.

---

### Struttura della schermata Recipes

```
┌─────────────────────────────────────────────────────────────────────┐
│  RECIPE BUILDER                                        [Idle badge]  │
├─────────────────────────────────────────────────────────────────────┤
│  Ricorda: usa external_measurement per LabVIEW (banner informativo) │
├─────────────────────────────────────────────────────────────────────┤
│  Recipe name: [Cryostat recipe            ]                          │
│                                                                      │
│  [Add point template]   [Add continuous-ramp template]   ← TEMPLATE │
├─────────────────────────────────────────────────────────────────────┤
│  Saved recipes: [dropdown]  [Load] [Save] [Rename] [Dup] [Delete]  │
├─────────────────────────────────────────────────────────────────────┤
│  Step: [Ramp T ▼]  Loop:[sample▼]  Target K:[4.2]  Rate:[1]  ...   │
│                                                          [Add]       │
├─────────────────────────────────────────────────────────────────────┤
│  STEPS (lista numerata)                                              │
│  1. ramp_temperature: sample → 300 K @ 1.0 K/min        [×]         │
│  2. external_measurement: point — measure_iv             [×]         │
│  ...                                                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Run controls                                                        │
│  [Start Recipe]  [Clear]  [Continue]  [Abort Recipe]                │
└─────────────────────────────────────────────────────────────────────┘
```

A destra della colonna principale c'è una **scheda di stato** che mostra la
ricetta attualmente in esecuzione e un riepilogo degli endpoint LabVIEW.

---

### Tipi di step disponibili

| Tipo (Step dropdown) | Cosa fa |
|----------------------|---------|
| **Ramp T** | Porta la temperatura al target alla velocità impostata |
| **Set T target** | Imposta il set point di temperatura (senza rampa controllata) |
| **Ramp B** | Porta il campo magnetico al target alla velocità impostata |
| **B to zero** | Porta il campo a zero |
| **Wait** | Aspetta un numero fisso di secondi |
| **Wait signal** | Aspetta che arrivi un segnale esterno (es. da LabVIEW) |
| **External measurement** | Coordina una misura con LabVIEW o altro software esterno |

---

### Aggiungere step manualmente

1. Seleziona il tipo di step dal menu a tendina **Step**.
2. Compila i campi che appaiono (cambiano in base al tipo scelto).
3. Clicca **Add** — lo step appare nella lista numerata.
4. Ripeti per ogni step della sequenza.
5. Per rimuovere uno step già aggiunto, clicca la **×** accanto ad esso.

---

### I bottoni template — Spiegazione dettagliata

Questi due pulsanti sono **scorciatoie**: aggiungono automaticamente uno o più
step pre-configurati con i valori più comuni, pronti da usare con LabVIEW.

---

#### Bottone: `Add point template`

**Aggiunge 1 step** di tipo `external_measurement` in modalità **point**.

Cosa succede quando la ricetta arriva a questo step:

1. La ricetta si **ferma** e aspetta.
2. Il software segnala a LabVIEW (o ad altro programma esterno) che è il momento
   di eseguire una misura (es. una curva IV).
3. LabVIEW esegue la misura.
4. LabVIEW notifica il completamento via HTTP (`POST /external-measurements/complete`).
5. La ricetta **riprende** con lo step successivo.

Valori pre-impostati dallo step generato:

| Campo | Valore |
|-------|--------|
| Mode | `point` |
| Request signal | `measure_iv` |
| Completion signal | `measure_iv.completed` |
| Failure signal | `measure_iv.failed` |
| Timeout | 600 secondi (10 min) |
| Message | "Run IV measurement in LabVIEW" |

**Quando usarlo**: quando vuoi fermarti a una temperatura/campo stabile e fare
una singola misura prima di continuare la sequenza.

**Esempio di ricetta tipica con point template**:

```
1. Ramp T: sample → 10 K @ 2 K/min
2. Wait: 60 s (stabilizzazione)
3. [point template] → LabVIEW misura IV a 10 K
4. Ramp T: sample → 20 K @ 2 K/min
5. Wait: 60 s
6. [point template] → LabVIEW misura IV a 20 K
...
```

---

#### Bottone: `Add continuous-ramp template`

**Aggiunge 3 step in un colpo solo**:

```
Step A:  external_measurement (mode: start)
Step B:  ramp_temperature (sample → 300 K @ 1.0 K/min)
Step C:  external_measurement (mode: stop)
```

Cosa succede quando la ricetta arriva a questi step:

1. **Step A** — Il software segnala a LabVIEW di **iniziare l'acquisizione continua**.
   LabVIEW conferma che l'acquisizione è partita, poi la ricetta procede.
2. **Step B** — La rampa di temperatura parte. LabVIEW acquisisce dati in
   continuo mentre la temperatura sale (tipico per misure R vs T).
3. **Step C** — La rampa è finita. Il software segnala a LabVIEW di **fermare
   l'acquisizione**. LabVIEW conferma, poi la ricetta continua.

Valori pre-impostati:

| Step | Campo | Valore |
|------|-------|--------|
| A (start) | Request signal | `R_vs_T.start` |
| B (ramp) | Target K | 300 K |
| B (ramp) | Rate K/min | 1.0 |
| B (ramp) | Loop | sample |
| C (stop) | Request signal | `R_vs_T.stop` |

**Quando usarlo**: per misure di resistenza in funzione della temperatura (R vs T)
o qualsiasi grandezza che LabVIEW deve acquisire in modo continuo mentre il
sistema rampeggia.

**Dopo aver cliccato il bottone** puoi modificare i valori dello step B (target,
velocità) cliccando sulla × e aggiungendolo di nuovo con i parametri giusti, oppure
modificare direttamente il JSON — ma la via più semplice è aggiustare dopo.

---

### Flusso completo: costruire e lanciare una ricetta

#### 1. Scrivi la ricetta

Usa il form (Add step manuale) e/o i bottoni template. Gli step appaiono nella
lista numerata man mano che li aggiungi.

#### 2. Dai un nome alla ricetta

Compila il campo **Recipe name** in alto (es. "R_vs_T_campione_A").

#### 3. Salva la ricetta (facoltativo ma consigliato)

Clicca **Save**. La ricetta viene salvata con il nome indicato e appare nel
menu a tendina **Saved recipes**. Puoi richiederla in futuro con **Load**.

Altri bottoni di gestione:

| Bottone | Azione |
|---------|--------|
| **Load** | Carica la ricetta selezionata nel dropdown |
| **Save** | Salva la ricetta corrente (non sovrascrive se esiste già, a meno che tu non confermi) |
| **Rename** | Rinomina la ricetta selezionata |
| **Duplicate** | Crea una copia con un nuovo nome |
| **Delete** | Elimina permanentemente la ricetta selezionata |

#### 4. Lancia la ricetta

Clicca **Start Recipe**. Il badge in alto passa da `Idle` a `Running`.

La ricetta parte immediatamente dall'alto e scende step per step. Lo stato
corrente appare nella scheda a destra:

- **Name**: nome della ricetta in esecuzione
- **Status**: `running`, `waiting`, `paused`, `completed`, `error`
- **Step**: quale step è attivo (es. "2 / 5")
- **Message**: messaggio descrittivo dello step

#### 5. Gestione durante l'esecuzione

| Bottone | Quando usarlo |
|---------|---------------|
| **Continue** | Quando la ricetta è in pausa su uno step `Wait signal` o `external_measurement` e vuoi farla procedere manualmente (senza aspettare LabVIEW) |
| **Abort Recipe** | Per fermare la ricetta immediatamente. Il sistema torna in IDLE ma **non** annulla automaticamente la rampa eventualmente in corso: usa `Hold B` o `Hold T` nel tab Commands se necessario |

#### 6. Fine ricetta

Quando tutti gli step sono completati, lo status diventa `completed` e il badge
torna `Idle`. I log sono già stati salvati automaticamente.

---

### Esempio pratico: R vs T da 4 K a 300 K con LabVIEW

**Obiettivo**: misurare la resistenza del campione in funzione della temperatura
mentre il sistema rampeggia da 4 K a 300 K.

**Costruzione della ricetta**:

1. Clicca **Add continuous-ramp template** — vengono aggiunti 3 step.
2. Il template usa `Target K = 300` e `Rate = 1.0 K/min`. Se vuoi partire da
   4 K, assicurati che il campione sia già a 4 K prima di avviare la ricetta.
3. Dai un nome alla ricetta, es. "R_vs_T_300K".
4. Salva con **Save**.
5. Clicca **Start Recipe**.

**Cosa accade**:

```
Step 1 (start):  Ricetta segnala a LabVIEW "R_vs_T.start"
                 LabVIEW risponde che l'acquisizione è avviata
Step 2 (ramp):   Temperatura sale da ~4 K a 300 K @ 1 K/min
                 LabVIEW acquisisce R in continuo
Step 3 (stop):   Ricetta segnala a LabVIEW "R_vs_T.stop"
                 LabVIEW ferma l'acquisizione e conferma
                 Ricetta: completed
```

---

### Esempio pratico: misure IV a temperature discrete

**Obiettivo**: misurare la curva IV a 10 K, 50 K, 100 K, 200 K, 300 K.

**Costruzione della ricetta**:

1. Aggiungi step manuale: **Ramp T**, sample, 10 K, 2 K/min.
2. Aggiungi step: **Wait**, 120 secondi (stabilizzazione).
3. Clicca **Add point template** — aggiunge lo step di misura IV.
4. Ripeti i passi 1–3 per 50 K, 100 K, 200 K, 300 K.
5. Salva la ricetta.
6. Avvia con **Start Recipe**.

**Risultato**: la ricetta si ferma automaticamente a ogni temperatura, aspetta
che LabVIEW finisca la misura IV, poi passa alla temperatura successiva.

---

## 10. Tab External Measurements

Pannello di monitoraggio per le misure esterne coordinate via HTTP.

Mostra:

- **Pending**: se c'è una richiesta di misura attiva (`true`/`false`)
- **Mode**: `point`, `start`, o `stop`
- **Request signal / Completion signal / Failure signal**: segnali configurati
- **Live measurement context**: valori in tempo reale che LabVIEW può leggere
  (temperatura campione, campo, magnete, ...)

### Endpoint che LabVIEW deve usare

| Endpoint | Metodo | Uso |
|----------|--------|-----|
| `/measurement-context` | GET | Legge temperatura, campo, stato in tempo reale |
| `/external-measurements/pending` | GET | Controlla se c'è una misura richiesta |
| `/external-measurements/complete` | POST | Comunica il completamento della misura |
| `/recipes/signal` | POST | Invia un segnale diretto alla ricetta |

LabVIEW può fare polling continuo a 1–5 Hz. Quando non c'è nessuna misura
attiva, `/external-measurements/pending` restituisce `{"pending": false}`.

> Non usare array anonimi come `[T, B]` in LabVIEW. Usa sempre i nomi espliciti
> dei campi: `sample_temperature_K`, `field_T`, `magnet_temperature_K`, ecc.

---

## 11. Tab Config

Pannello di ispezione e configurazione avanzata.

Mostra:

- **Config snapshot**: riepilogo del file di configurazione caricato (backend,
  porta, indirizzi VISA, preset sensore, ...)
- **Insert profiles**: profili delle sonde disponibili (Fisher, Basic, Heliox).
  Puoi cambiare l'insert attivo cliccando su un profilo — questo ricarica il
  mapping dei canali senza riavviare il servizio.
- **Sample sensor presets**: preset predefiniti per il sensore di temperatura
  del campione. Seleziona un preset e clicca **Apply** per cambiare il sensore
  attivo.

---

## 12. Log Viewer (strumento offline)

Il Log Viewer è uno strumento **separato** (Streamlit) per analizzare i file CSV
di log fuori dalla sessione di controllo.

### Avvio

Da un terminale:

```bash
teslatron-log
```

Oppure, dalla GUI: pulsante **Open Log Viewer** nella barra superiore (avvia
Streamlit automaticamente se non è già in esecuzione).

Poi apri il browser a:

```
http://127.0.0.1:8501/
```

### Utilizzo

1. Carica uno o più file CSV dalla cartella `data/`.
2. Visualizza i trace sovrapposti con Plotly (zoom, pan, selezione canali).
3. Naviga gli eventi ricostruiti (ricette, rampe, segnali).
4. Anteprima del dataframe grezzo nella scheda separata.

### Arresto

- `Ctrl+C` nel terminale dove gira Streamlit.
- Oppure: `teslatron-stop --include-log-viewer`.

---

## 13. Regole di sicurezza

### Regola 1 — Parti sempre in sola lettura

Usa `teslatron readonly` per la prima connessione al lab. Verifica che i valori
siano sensati prima di abilitare i comandi.

### Regola 2 — LabVIEW e Python non contemporaneamente

Non tenere LabVIEW connesso ai Mercury iTC/iPS mentre Python è in funzione.
I due client entrano in conflitto sulla sessione VISA e le connessioni si
resettano.

### Regola 3 — Rampe piccole prima

Prima di testare una rampa grande, testa con un delta piccolo (es. ±1 K, ±0.1 T).

### Regola 4 — Hold se qualcosa è anomalo

Se i valori sembrano sbagliati, usa subito **Hold B** o **Hold T** nel tab Commands. Poi
analizza la situazione.

### Regola 5 — Non chiudere il terminale senza fermare il servizio

Chiudere solo il browser non ferma il backend. Usa `teslatron-stop` o `Ctrl+C`
nel terminale prima di lasciare il lab.

---

## 14. Risoluzione dei problemi comuni

### I valori non si aggiornano

- Il backend è partito? Controlla il terminale.
- Il config punta a indirizzi VISA validi?
- Un altro client (LabVIEW) tiene aperta la sessione Mercury?

### Il pulsante di comando non risponde

- Controlla la barra di stato: **Access** deve essere `Writable`.
- Se è `READ ONLY`, riavvia con `teslatron control`.

### La ricetta si ferma su un step `external_measurement` e non procede

- LabVIEW deve rispondere con `POST /external-measurements/complete`.
- Se LabVIEW non è disponibile, usa il pulsante **Continue** per sbloccare
  manualmente lo step e far procedere la ricetta.

### Errore 403 da un endpoint

Il config caricato ha `read_only: true`. Controlla `GET /config` per
confermare, poi riavvia con il config di controllo.

### Il log viewer non mostra dati recenti

I file CSV vengono scritti nella cartella `data/`. Assicurati di caricare
il file della data corrente (es. `cryostat_environment_2026-06-17.csv`).

Le versioni recenti del servizio scrivono i CSV con le colonne numeriche nelle
prime posizioni e gli status nelle colonne successive. Se nella stessa data era
già presente un file con header storico, il servizio non lo sovrascrive e crea
un file separato come `cryostat_environment_2026-06-17_v2.csv`. Il log viewer
supporta entrambi i formati.

### Il servizio era già in ascolto su quella porta

Usa `teslatron-stop` per liberare le porte, poi riavvia.

---

## 15. Glossario

| Termine | Significato |
|---------|-------------|
| **Backend** | Il driver che parla con l'hardware: `mock`, `standard`, `heliox` |
| **ITC** | Mercury iTC — controllore di temperatura |
| **IPS** | Mercury iPS — alimentatore del magnete superconduttore |
| **VTI** | Variable Temperature Insert — circuito di raffreddamento ausiliario |
| **Sample loop** | Loop di controllo temperatura del campione |
| **Hold B / Hold T** | Modalità di stasi: mantengono rispettivamente il campo o la temperatura attuale senza rampe |
| **Ricetta (Recipe)** | Sequenza programmata di operazioni da eseguire in automatico |
| **Step** | Singola operazione all'interno di una ricetta |
| **Template** | Set di step pre-configurati da aggiungere con un solo clic |
| **Point measurement** | Misura eseguita a un punto fisso: la ricetta si ferma e aspetta LabVIEW |
| **Continuous-ramp** | Acquisizione continua durante una rampa di temperatura |
| **Switch heater** | Riscaldatore del persistent switch del magnete |
| **VISA** | Standard di comunicazione con strumentazione (National Instruments) |
| **Mock** | Simulazione offline: nessun hardware collegato |
| **Read only** | Modalità sola lettura: comandi bloccati per sicurezza |
| **Writable** | Modalità controllo: i comandi sono abilitati |
| **Pending** | Richiesta di misura esterna in attesa di risposta da LabVIEW |
| **Recipe dir** | Cartella dove vengono salvate le ricette in formato JSON |
| **Log dir** | Cartella dove vengono salvati i file CSV di ambiente |
