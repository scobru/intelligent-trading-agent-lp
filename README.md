# Intelligent Trading Agent - Concentrated Liquidity LP (Base L2)

**English** · [Italiano](README.it.md)

> ⚠️ **Experimental software, not financial advice.** The bot trades real money on Base and can lose some or all of the capital you give it. Start with paper trading or dry-run; when you go live, use a dedicated wallet and only amounts you can afford to lose. See the **Disclaimer** section at the bottom.

An autonomous agent that actively provides **concentrated liquidity** on
**Uniswap V3** (Aerodrome Slipstream compatible) on **Base (chain ID 8453)**,
with continuous **impermanent loss (IL)** monitoring, automatic fee collection
and **dynamic re-centering** of the range.

It is part of the [Intelligent Trading](https://github.com/scobru/intelligent-trading)
suite of agents for Base.

---

## Main features

1. **Concentrated liquidity on Uniswap V3**:
   - Provides liquidity in a narrow, optimized range (e.g. $\pm 4\%$ around the
     current price for WETH/USDC on the 0.05% pool).
   - Capital efficiency multiplier (concentration factor up to $20\times - 30\times$)
     compared to traditional v2 liquidity.
   - Exact tick math ($1.0001^{tick}$), conversions to/from `sqrtPriceX96` and
     liquidity $L$ estimation.

2. **Dynamic re-centering**:
   - Watches whether the price leaves the range or enters the edge buffer.
   - Anti-whipsaw protection: minimum holding time
     (`MIN_HOLD_HOURS_BEFORE_RECENTER`) to avoid constant repositioning on
     short-lived volatile breakouts.
   - Re-centering procedure:
     1. Collects the accrued fees (`collect`).
     2. Removes liquidity from the old position (`decreaseLiquidity`).
     3. Rebalances the tokens to the 50/50 ratio required by the new center price.
     4. Mints the new NFT position (`mint`) centered on the new market price.

3. **Impermanent loss analytics**:
   - Constantly compares the current value of the LP position with the **HODL**
     benchmark ($V_{lp}$ vs $V_{hodl}$).
   - Computes net P&L including all accrued swap fees.

4. **AI reasoning with a deterministic fallback**:
   - Analyzes market structure through an **OpenRouter LLM** (trending vs
     ranging regimes).
   - Falls back immediately to fixed mathematical rules if the network or the
     LLM API is unavailable.

5. **Faithful paper trading simulator**:
   - Models hourly swap fee accrual from the pool's fee tier and the
     concentration factor.
   - Simulates gas, slippage and realignment swaps without risking real capital.

6. **Web dashboard & Telegram bot**:
   - Standalone native HTTP dashboard:
     - interactive bar showing where the price sits inside the range;
     - real-time status badge (IN-RANGE / OUT-OF-RANGE);
     - impermanent loss and net P&L metrics;
     - full history of mint, recenter and collect operations;
     - buttons to start cycles or re-center manually (protected by
       `DASHBOARD_RUN_TOKEN`).
   - Proactive notifications and interactive Telegram commands (`/status`,
     `/position`, `/recenter`, `/collect`, `/help`).

---

## Architecture

```
                            ┌────────────────────────┐
                            │    Uniswap V3 Pool     │
                            │   (slot0: sqrtP, tick) │
                            └───────────┬────────────┘
                                        │ Current price & tick
                                        ▼
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│    Position Tracker    │──>│       LP Manager       │<──│     OpenRouter LLM     │
│  (IL vs HODL, Range)   │   │     (LP planning)      │   │ (Regime & Range Width) │
└────────────────────────┘   └───────────┬────────────┘   └────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     ┌───────────────────────┐                       ┌───────────────────────┐
     │      Paper Book       │                       │  NonfungiblePosMgr    │
     │  (Range simulation)   │                       │  (Live mint/collect)  │
     └───────────────────────┘                       └───────────────────────┘
```

---

## Installation and configuration

### 1. Environment
```bash
cp .env.example .env
```

Edit `.env`:
```ini
BASE_RPC_URL=https://mainnet.base.org
WALLET_ADDRESS=0x...
PRIVATE_KEY=...
# start safe: set both to false only when you are ready to go live
DRY_RUN=true
PAPER_TRADING=true

# LP pool pair
POOL_TOKEN0=WETH
POOL_TOKEN1=USDC
POOL_FEE=500  # 0.05%

# Range width (+/- 4.0%)
RANGE_WIDTH_PCT=8.0
MIN_HOLD_HOURS_BEFORE_RECENTER=2.0
```

### 2. Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run
```bash
python main.py
```

The web dashboard listens on `http://localhost:3000` (`DASHBOARD_PORT`, or
the standard `PORT` variable passed by Docker / CapRover).

---

## Docker and CapRover

```bash
docker build -t intelligent-trading-agent-lp .
docker run -d --name lp-agent -p 3000:3000 --env-file .env intelligent-trading-agent-lp
```

CapRover deployment through the `captain-definition` file.

---

## Tests

```bash
python -m unittest discover tests
```

---

## ⚠️ Disclaimer

This software is experimental and provided "as is", without warranty of any
kind (see the MIT license). It is not financial advice nor an invitation to
invest.

- **You can lose money.** Bugs, wrong model decisions, slippage, protocol
  exploits, manipulated oracles and liquidations can cause the loss of some or
  all of your capital.
- **Decisions are made by an LLM.** It can be wrong or behave unpredictably:
  the executor's limits reduce the damage, they do not eliminate it. Past
  results, paper ones included, do not guarantee future ones.
- **Start with paper or dry-run.** When live, use a wallet dedicated to the
  bot, with amounts you can afford to lose, and never reuse that private key
  elsewhere.
- **Protect your keys.** The private key belongs only in the deployment's
  environment variables: never commit it. Without `DASHBOARD_RUN_TOKEN` the
  dashboard commands stay disabled: set it to a long random value before
  exposing the dashboard to the Internet.
- **Laws and taxes.** You are responsible for complying with the rules and tax
  obligations of your country.
- **Impermanent loss.** Providing concentrated liquidity exposes you to losses
  compared to simply holding the tokens when the price leaves the range; fees
  do not always make up for them.

## License
MIT
