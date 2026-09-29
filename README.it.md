# Intelligent Trading Agent - Concentrated Liquidity LP (Base L2)

[English](README.md) · **Italiano**

> ⚠️ **Software sperimentale, non consulenza finanziaria.** Il bot opera con denaro reale su Base e può perdere in parte o del tutto il capitale che gli affidi. Parti in paper trading o dry-run; in live usa un wallet dedicato e solo importi che puoi permetterti di perdere. Dettagli nella sezione **Avvertenza** in fondo.

Agente autonomo per la fornitura attiva di **Liquidità Concentrata** su **Uniswap V3** (e compatibile Aerodrome Slipstream) su rete **Base (Chain ID 8453)** con monitoraggio continuo dell'**Impermanent Loss (IL)**, raccolta automatica delle commissioni e **riposizionamento (re-centering) dinamico** del range.

Parte della suite di trading agent modulari per Base (`intelligent-trading-agent`, `intelligent-trading-agent-neutral`, `intelligent-trading-agent-degen`, `intelligent-trading-agent-yield`, `intelligent-trading-agent-dca`, `intelligent-trading-agent-lp`).

---

## Caratteristiche Principali

1. **Liquidità Concentrata su Uniswap V3**:
   - Fornisce liquidità in un range ristretto e ottimizzato (es. $\pm 4\%$ attorno al prezzo corrente per WETH/USDC al pool 0.05%).
   - Moltiplicatore di efficienza del capitale (concentration factor fino a $20\times - 30\times$) rispetto alla liquidità v2 tradizionale.
   - Calcolo matematico esatto dei tick ($1.0001^{tick}$), conversioni da/a `sqrtPriceX96` e stima della liquidità $L$.

2. **Riposizionamento Dinamico (Re-centering)**:
   - Monitora se il prezzo esce dal range o entra nella fascia di buffer limite.
   - Protezione anti-whipsaw: tempo minimo di permanenza (`MIN_HOLD_HOURS_BEFORE_RECENTER`) per evitare riposizionamenti continui su breakout volatili transitori.
   - Procedura atomica:
     1. Riscuote le commissioni accumulate (`collect`).
     2. Rimuove la liquidità dalla vecchia posizione (`decreaseLiquidity`).
     3. Ribilancia i token al rapporto 50/50 richiesto per il nuovo centro prezzo.
     4. Conia la nuova posizione NFT (`mint`) centrata sul nuovo prezzo di mercato.

3. **Monitoraggio Analitico dell'Impermanent Loss (IL)**:
   - Confronta costantemente il controvalore corrente della posizione LP con la strategia di riferimento **HODL** ($V_{lp}$ vs $V_{hodl}$).
   - Calcola il PnL netto comprensivo di tutte le commissioni di swap maturate.

4. **Ragionamento AI con Fallback Deterministico**:
   - Analizza la struttura di mercato tramite **OpenRouter LLM** (regimi di trending vs oscillazione in range).
   - Fallback immediato a regole matematiche certe in caso di indisponibilità della rete o delle API LLM.

5. **Simulatore Paper Trading Fedele**:
   - Modella l'accumulo orario delle fee di swap basato sul fee tier del pool e sull'indice di concentrazione.
   - Simula gas, slippage e swap di riallineamento senza esporre capitale reale.

6. **Dashboard Web & Telegram Bot**:
   - Dashboard HTTP standalone nativa:
     - Barra interattiva di posizionamento all'interno del range (con indicatore visivo del prezzo).
     - Badge di stato in tempo reale (IN-RANGE / OUT-OF-RANGE).
     - Metriche di Impermanent Loss e PnL netto.
     - Storico completo delle operazioni di mint, recenter e collect.
     - Pulsanti per avviare cicli o eseguire re-center manuali.
   - Notifiche proattive e comandi interattivi su Telegram (`/status`, `/position`, `/recenter`, `/collect`, `/help`).

---

## Architettura del Sistema

```
                            ┌────────────────────────┐
                            │    Uniswap V3 Pool     │
                            │   (slot0: sqrtP, tick) │
                            └───────────┬────────────┘
                                        │ Prezzo & Tick Correnti
                                        ▼
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│    Position Tracker    │──>│       LP Manager       │<──│     OpenRouter LLM     │
│  (IL vs HODL, Range)   │   │  (Pianificazione LP)   │   │ (Regime & Range Width) │
└────────────────────────┘   └───────────┬────────────┘   └────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌───────────────────────┐                       ┌───────────────────────┐
     │      Paper Book       │                       │  NonfungiblePosMgr    │
     │  (Simulazione Range)  │                       │   (Mint/Collect Live) │
     └───────────────────────┘                       └───────────────────────┘
```

---

## Installazione e Configurazione

### 1. Configurazione Ambiente
```bash
cp .env.example .env
```

Modifica `.env`:
```ini
BASE_RPC_URL=https://mainnet.base.org
WALLET_ADDRESS=0x...
PRIVATE_KEY=...
DRY_RUN=false
PAPER_TRADING=false

# Coppia del pool LP
POOL_TOKEN0=WETH
POOL_TOKEN1=USDC
POOL_FEE=500  # 0.05%

# Ampiezza del range (+/- 4.0%)
RANGE_WIDTH_PCT=8.0
MIN_HOLD_HOURS_BEFORE_RECENTER=2.0
```

### 2. Installazione Dipendenze
```bash
pip install -r requirements.txt
```

### 3. Avvio
```bash
python main.py
```

La dashboard web sarà disponibile su `http://localhost:8080`.

---

## Esecuzione con Docker e CapRover

```bash
docker build -t intelligent-trading-agent-lp .
docker run -d --name lp-agent -p 8080:8080 --env-file .env intelligent-trading-agent-lp
```

Deployment su CapRover tramite file `captain-definition`.

---

## Test della Suite

```bash
python -m unittest discover tests
```

---

## ⚠️ Avvertenza

Questo software è sperimentale ed è fornito "così com'è", senza garanzie di alcun tipo
(vedi la licenza MIT). Non è consulenza finanziaria né un invito a investire.

- **Puoi perdere denaro.** Bug, decisioni sbagliate del modello, slippage, exploit dei protocolli,
  oracoli manipolati e liquidazioni possono far perdere in parte o del tutto il capitale.
- **Le decisioni le prende un LLM.** Può sbagliare o comportarsi in modo imprevedibile: i limiti
  dell'esecutore riducono il danno, non lo azzerano. I rendimenti passati, anche in paper, non
  garantiscono quelli futuri.
- **Parti in paper o dry-run.** In live usa un wallet dedicato al bot, con importi che puoi
  permetterti di perdere, e non riutilizzare quella chiave privata altrove.
- **Proteggi le chiavi.** La chiave privata va solo nelle variabili d'ambiente del deploy: non
  committarla mai. Senza `DASHBOARD_RUN_TOKEN` i comandi della dashboard restano disattivati:
  impostalo con un valore lungo e casuale prima di esporla su Internet.
- **Leggi e tasse.** Sei responsabile del rispetto delle norme e degli obblighi fiscali del tuo paese.
- **Impermanent loss.** Fornire liquidità concentrata espone a perdite rispetto al semplice possesso dei token quando il prezzo esce dal range; le commissioni non sempre le compensano.

## Licenza
MIT
