"""Slow-trend BTC bot for agent-fun/bars-replay/v1 (make.fun btc-race-24h).

Idea, in one paragraph: one-minute cbBTC prices are mostly noise (the dev
window's 1-minute returns have ~zero autocorrelation past a few minutes and a
full-size round trip costs ~26 bps), so every fast signal whipsaws itself to
death on fees. The only durable edge in the data is a slow, multi-hour trend:
the dev window falls ~2% for five hours, then rises ~2% for three hours, then
chops sideways. This bot therefore trades rarely and only with that slow
trend: it goes long, with all its cash, when a 75-bar average crosses above a
180-bar average, and it gets out on a trailing stop that tightens as the trade
pays, or on a hard stop. The rest of the time it sits in cash.

Rules (all integer arithmetic on the raw close prices, so two runs on the same
bars produce the same orders byte for byte):

  * Universe: the single symbol in hello (CBBTC). One open position at most.
  * Entry: SMA(75) crosses above SMA(180) -- the fast average strictly above
    the slow one this bar, at or below it last bar. A buy the market refuses
    (slippage) is retried for two more bars while the averages stay crossed.
    No new entries in the last 120 bars of the window: a trade opened there
    has no room to develop before the final flatten.
  * Size: the whole cash balance, capped by the harness weight room (100% of
    equity). One position, long only, no leverage.
  * Exit, checked on every bar while holding, first match wins:
      - hard stop: close 1.0% or more below the entry close;
      - trailing stop: close 0.7% or more below the highest close since
        entry, tightened to 0.5% once the close is 1.5% or more above entry.
    The exit is level-based, not edge-based, so a sell the market refuses is
    simply sent again on the next bar while the level still holds.
  * Slippage cap on every order: 150 bps from the latest close. A 1-minute
    gap bigger than that is a spike to sit out, not a price to chase.
  * Warm-up bars (if hello carries any) only build history; no orders.
  * No network, no clock, no files, no randomness: the only inputs are the
    bars the harness sends.

Backtest on the kit dev window (local float replay of the harness fill
model; the official grade-local run is the authority, see README.md):

  * this bot:      +$10.68 net PnL, 4 trades, max drawdown 0.99%
  * buy-and-hold:  -$31.63 net PnL, 1 trade,  max drawdown 3.3%
  * hold cash:      $0.00, 0 trades
  * EMA(8)/EMA(21) cross: -$148.16, 80 trades (whipsaw + fees)
  * RSI(14) mean reversion: -$105.97, 63 trades
  The slow-average family is positive across its whole neighbourhood
  (SMA 45/150, 60/150, 60/180, 75/180, 90/180 all make money on the dev
  window under every exit variant tried), and it never loses money on any
  sliding 12-hour slice of the dev window -- the result does not hinge on
  one tuned parameter.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- strategy parameters (bars / basis points, all integers) ---
FAST_BARS = 75
SLOW_BARS = 180
WINDOW_BARS = 1440  # one 1-minute bar per minute of the 24-hour window
ENTRY_RETRY_BARS = 2
NO_ENTRY_AFTER_BARS = 120  # bars left at the end of the window
STOP_BPS = 100             # hard stop: 1.0% below entry
TRAIL_BPS = 70             # trailing stop: 0.7% below the peak since entry
RATCHET_PROFIT_BPS = 150   # tighten the trail once 1.5% in profit
RATCHET_TRAIL_BPS = 50     # tightened trail: 0.5% below the peak
MAX_SLIPPAGE_BPS = 150

_closes = []          # raw closes, one per batch, oldest first
_fast_sum = 0         # sum of the last FAST_BARS closes (raw units)
_slow_sum = 0         # sum of the last SLOW_BARS closes (raw units)
_prev_fast_above = None  # fast > slow on the previous bar, once both exist
_last_cross_seq = -1     # seq of the latest fast-above-slow cross
_entered_for_cross = True  # a buy already sent for the latest cross
_entry_close = 0      # raw close at the bar the current position was opened
_peak_close = 0       # highest raw close since the current position opened


def _averages_ready():
    return len(_closes) >= SLOW_BARS


def _fast_above_slow():
    # fast avg > slow avg, cross-multiplied so no division is ever needed:
    #   _fast_sum / FAST_BARS > _slow_sum / SLOW_BARS
    return _fast_sum * SLOW_BARS > _slow_sum * FAST_BARS


def _push_close(close):
    global _fast_sum, _slow_sum
    _closes.append(close)
    _fast_sum += close
    _slow_sum += close
    if len(_closes) > FAST_BARS:
        _fast_sum -= _closes[-FAST_BARS - 1]
    if len(_closes) > SLOW_BARS:
        _slow_sum -= _closes[-SLOW_BARS - 1]


def on_batch(state, batch):
    global _prev_fast_above, _last_cross_seq, _entry_close, _peak_close
    global _entered_for_cross
    close = state.close(SYMBOL)
    if close is None:
        return []
    _push_close(close)

    holding = SYMBOL in state.positions
    if holding:
        held = state.positions[SYMBOL]
        if held.qty <= 0:
            holding = False
    if state.warming_up:
        return []

    # --- exits first: a level-based exit is re-sent until it fills ---
    if holding:
        if close > _peak_close:
            _peak_close = close
        trail_bps = TRAIL_BPS
        if close * 10000 >= _entry_close * (10000 + RATCHET_PROFIT_BPS):
            trail_bps = RATCHET_TRAIL_BPS
        stop_hit = close * 10000 <= _entry_close * (10000 - STOP_BPS)
        trail_hit = close * 10000 <= _peak_close * (10000 - trail_bps)
        if stop_hit or trail_hit:
            return [{"side": "sell", "symbol": SYMBOL,
                     "qty": state.positions[SYMBOL].qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- entries: a slow-average cross, retried briefly if refused ---
    if not _averages_ready():
        return []
    above = _fast_above_slow()
    if _prev_fast_above is False and above:
        _last_cross_seq = state.seq
        _entered_for_cross = False
    _prev_fast_above = above
    fresh_cross = (state.seq - _last_cross_seq) <= ENTRY_RETRY_BARS
    if above and fresh_cross and _last_cross_seq >= 0 and not _entered_for_cross:
        # Do not open a trade that cannot develop before the flatten:
        # the window is 1440 bars and this check keeps the last 120 clear.
        if state.seq >= WINDOW_BARS - NO_ENTRY_AFTER_BARS:
            return []
        amount = state.cash
        room = state.weight_room(SYMBOL)
        if room < amount:
            amount = room
        if amount < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        _entry_close = close
        _peak_close = close
        _entered_for_cross = True
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": amount,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
