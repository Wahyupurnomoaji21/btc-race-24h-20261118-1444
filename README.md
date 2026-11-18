# BTC Race 24h — slow-trend bot

Entry for make.fun `btc-race-24h-20261118-1444` ($250, top 10 paid).
Bot paper-trades cbBTC/USDC on 1-minute bars, starting from 1,000 USDC.
Entrypoint: `main.py` (Python 3.13, standard library only).

## Strategy

One-minute cbBTC prices are mostly noise: 1-minute returns have ~zero
autocorrelation past a few minutes, and a full-size round trip costs ~26 bps
in fees, slippage and price impact. Fast signals whipsaw themselves to death
on fees. The only durable edge in the dev window is a slow, multi-hour trend,
so the bot trades rarely and only with it:

- **Entry:** SMA(75) crosses above SMA(180), full cash, long only.
  A refused buy is retried for two bars. No entries in the last 120 bars.
- **Exit:** hard stop 1.0% below entry; trailing stop 0.7% below the peak
  since entry, tightened to 0.5% once 1.5% in profit. Level-based, so a
  refused sell is re-sent until it fills.
- **Slippage cap:** 150 bps from the latest close on every order.
- **Otherwise:** sits in cash. All arithmetic is integer math on raw closes,
  so two runs on the same bars produce identical orders.

See the docstring at the top of `main.py` for the full rules.

## Local validation (dev window, kit dataset)

Float replay of the harness fill model (the official `grade-local` run is
the authority):

| Strategy | Net PnL | Trades | Max drawdown |
|---|---:|---:|---:|
| **This bot (SMA 75/180 + trail/ratchet)** | **+$10.68** | 4 | 0.99% |
| Buy-and-hold | -$31.63 | 1 | 3.3% |
| Hold cash | $0.00 | 0 | 0.0% |
| EMA(8)/EMA(21) cross | -$148.16 | 80 | 15.0% |
| RSI(14) mean reversion | -$105.97 | 63 | 10.5% |

The slow-average family is positive across its whole neighbourhood
(SMA 45/150, 60/150, 60/180, 75/180, 90/180 all make money on the dev
window under every exit variant tried), and it never loses money on any
sliding 12-hour slice of the dev window.

## Grade locally

From the kit directory:

```
node cli/makefun.mjs grade-local --kit . --bot <this repo> --driver process
```

## Layout

- `main.py` — the bot (written fresh for this entry).
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim.
- `.makefun/freshness.json` — drand Quicknet anchor for this entry.
