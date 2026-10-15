"""
CryptoScan Paper Bot - single-file version (Streamlit + Binance public data).
PAPER TRADING ONLY: no API keys, no real money.

Run:  streamlit run bot.py
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

BASES = ["https://api.binance.com", "https://data-api.binance.vision"]
FEE = 0.001
START_CASH = 1000.0
MAX_EQUITY_POINTS = 5000
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")

_CACHE = {}
_LOCK = threading.Lock()
_SESSION = requests.Session()


# --------------------------------------------------------------------------
# Market data (fetched in parallel, cached for a few seconds)
# --------------------------------------------------------------------------
def fetch_one(symbol, interval, limit=500, ttl=15):
    key = (symbol, interval, limit)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1].copy()

    last_err = None
    for base in BASES:
        try:
            r = _SESSION.get(
                f"{base}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            df = pd.DataFrame(r.json()).iloc[:, :6]
            df.columns = ["time", "open", "high", "low", "close", "volume"]
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            with _LOCK:
                _CACHE[key] = (now, df)
            return df.copy()
        except Exception as e:
            last_err = e
    raise RuntimeError(str(last_err))


def fetch_all(symbols, interval, limit=500, ttl=15):
    data, errors = {}, {}
    if not symbols:
        return data, errors

    def work(sym):
        try:
            return sym, fetch_one(sym, interval, limit, ttl), None
        except Exception as e:
            return sym, None, str(e)

    with ThreadPoolExecutor(max_workers=min(10, len(symbols))) as ex:
        for sym, df, err in ex.map(work, symbols):
            if err:
                errors[sym] = err
            else:
                data[sym] = df
    return data, errors


# --------------------------------------------------------------------------
# Indicators and strategy
# --------------------------------------------------------------------------
def add_indicators(df, ema_len=200):
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
    df["ema"] = df["close"].ewm(span=ema_len, adjust=False).mean()
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df


def entry_signal(row, prev_rsi, p):
    return bool(row["close"] > row["ema"] and prev_rsi < p["rsi_entry"] <= row["rsi"])


def position_size(equity, cash, price, atr, p):
    stop_dist = atr * p["atr_mult"]
    if stop_dist <= 0 or price <= 0:
        return 0.0
    qty = equity * p["risk_pct"] / 100 / stop_dist
    qty = min(qty, equity * p["max_alloc"] / 100 / price, cash / (price * (1 + FEE)))
    return max(qty, 0.0)


# --------------------------------------------------------------------------
# Paper portfolio (saved to paper_state.json)
# --------------------------------------------------------------------------
def default_state():
    return dict(cash=START_CASH, positions={}, last_candle={}, trades=[], equity_hist=[])


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                state = {**default_state(), **saved}
                # ضمان سلامة الأنواع وعدم وجود قيمة None تسبب أخطاء
                if not isinstance(state.get("last_candle"), dict):
                    state["last_candle"] = {}
                if not isinstance(state.get("positions"), dict):
                    state["positions"] = {}
                if not isinstance(state.get("trades"), list):
                    state["trades"] = []
                if not isinstance(state.get("equity_hist"), list):
                    state["equity_hist"] = []
                return state
        except Exception:
            pass
    return default_state()


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def close_position(state, sym, price, reason, when):
    pos = state["positions"].pop(sym)
    proceeds = pos["qty"] * price * (1 - FEE)
    pnl = proceeds - pos["cost"]
    state["cash"] += proceeds
    state["trades"].append(dict(
        coin=sym.replace("USDT", ""), entry_time=pos["entry_time"], exit_time=when,
        entry=pos["entry"], exit=price, pnl=round(pnl, 2),
        pnl_pct=round(pnl / pos["cost"] * 100, 2), reason=reason))


def market_value(state, prices):
    return state["cash"] + sum(
        v["qty"] * prices.get(s, v["entry"]) for s, v in state["positions"].items())


def live_step(data, p, state, allowed):
    prices = {s: float(d.iloc[-1]["close"]) for s, d in data.items()}
    now_ts = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    pos = state["positions"]

    for sym in list(pos):
        if sym not in prices:
            continue
        px, last = prices[sym], data[sym].iloc[-2]
        reason = None
        if px <= pos[sym]["stop"]:
            reason = "Stop Loss"
        elif px >= pos[sym]["tp"]:
            reason = "Take Profit"
        elif last["rsi"] > p["rsi_exit"]:
            reason = "RSI Exit"
        if reason:
            close_position(state, sym, px, reason, now_str)

    equity = market_value(state, prices)
    for sym in sorted(allowed):
        if sym not in data:
            continue
        df = data[sym]
        last, prev = df.iloc[-2], df.iloc[-3]
        cid = str(last["time"])
        if sym in pos:
            state["last_candle"][sym] = cid
            continue
        if state["last_candle"].get(sym) == cid:
            continue
        state["last_candle"][sym] = cid
        if len(pos) >= p["max_pos"]:
            continue
        if entry_signal(last, prev["rsi"], p):
            px = prices[sym]
            qty = position_size(equity, state["cash"], px, last["atr"], p)
            if qty * px >= 10:
                risk = last["atr"] * p["atr_mult"]
                cost = qty * px * (1 + FEE)
                state["cash"] -= cost
                pos[sym] = dict(qty=qty, entry=px, stop=px - risk, tp=px + risk * p["rr"],
                                cost=cost, entry_time=now_str)

    equity = market_value(state, prices)
    hist = state["equity_hist"]
    if not hist or now_ts - hist[-1][0] >= 60:
        hist.append([int(now_ts), round(equity, 2)])
        del hist[:-MAX_EQUITY_POINTS]
    return prices, equity


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------
def backtest(df, p):
    cash, pos = START_CASH, None
    trades, equity, times = [], [], []
    start = max(p["ema_len"], 30)
    for i in range(start, len(df)):
        row, prev = df.iloc[i], df.iloc[i - 1]
        if pos:
            exit_price, reason = None, None
            if row["low"] <= pos["stop"]:
                exit_price, reason = min(pos["stop"], row["open"]), "Stop Loss"
            elif row["high"] >= pos["tp"]:
                exit_price, reason = max(pos["tp"], row["open"]), "Take Profit"
            elif row["rsi"] > p["rsi_exit"]:
                exit_price, reason = row["close"], "RSI Exit"
            if exit_price is not None:
                proceeds = pos["qty"] * exit_price * (1 - FEE)
                pnl = proceeds - pos["cost"]
                cash += proceeds
                trades.append(dict(time=row["time"], pnl=pnl, pnl_pct=pnl / pos["cost"] * 100, reason=reason))
                pos = None
        if pos is None and entry_signal(row, prev["rsi"], p):
            qty = position_size(cash, cash, row["close"], row["atr"], p)
            if qty * row["close"] >= 10:
                risk = row["atr"] * p["atr_mult"]
                cost = qty * row["close"] * (1 + FEE)
                cash -= cost
                pos = dict(qty=qty, stop=row["close"] - risk, tp=row["close"] + risk * p["rr"], cost=cost)
        equity.append(cash + (pos["qty"] * row["close"] if pos else 0.0))
        times.append(row["time"])
    return pd.DataFrame(trades), pd.Series(equity, index=times, dtype=float)


def stats(trades, eq):
    out = dict(ret=0.0, dd=0.0, trades=0, win_rate=0.0, pf=None)
    if len(eq):
        out["ret"] = (eq.iloc[-1] / START_CASH - 1) * 100
        out["dd"] = ((eq / eq.cummax()) - 1).min() * 100
    if len(trades):
        wins = trades.loc[trades["pnl"] > 0, "pnl"].sum()
        losses = -trades.loc[trades["pnl"] <= 0, "pnl"].sum()
        out["trades"] = len(trades)
        out["win_rate"] = (trades["pnl"] > 0).mean() * 100
        out["pf"] = wins / losses if losses > 0 else None
    return out


# ==========================================================================
# Dashboard (UI)
# ==========================================================================
st.set_page_config(page_title="CryptoScan Paper Bot", page_icon="📈", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2rem;}
.hero {background: linear-gradient(120deg,#0f2027,#203a43,#2c5364);
       padding: 22px 28px; border-radius: 16px; margin-bottom: 18px;}
.hero h1 {margin: 0; font-size: 2rem; color: #ffffff;}
.hero p {margin: 4px 0 0; color: #b8c7d1;}
[data-testid="stMetric"] {background: rgba(128,128,128,0.08);
       border: 1px solid rgba(128,128,128,0.25); padding: 14px 16px; border-radius: 12px;}
</style>
<div class="hero">
  <h1>📈 CryptoScan Paper Bot</h1>
  <p>Multi-coin scanner · RSI + EMA trend filter · ATR risk management · Paper trading only</p>
</div>
""", unsafe_allow_html=True)

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT"]


def show_df(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)


def fmt_price(x):
    return f"${x:,.2f}" if x >= 1 else f"${x:.5f}"


with st.sidebar:
    st.header("⚙️ Settings")
    symbols = st.multiselect("Coins to scan", ALL_SYMBOLS, default=ALL_SYMBOLS[:8])
    interval = st.selectbox("Timeframe", ["5m", "15m", "1h", "4h"], index=2)

    st.subheader("Strategy")
    rsi_entry = st.slider("RSI entry (cross up)", 20, 50, 40)
    rsi_exit = st.slider("RSI exit (above)", 55, 85, 70)
    atr_mult = st.slider("Stop loss (ATR x)", 1.0, 4.0, 2.0, 0.5)
    rr = st.slider("Take profit (Risk:Reward)", 1.0, 4.0, 2.0, 0.5)
    ema_len = st.number_input("Trend EMA length", 50, 300, 200, 10)

    st.subheader("Portfolio")
    max_pos = st.slider("Max open positions", 1, 10, 4)
    risk_pct = st.slider("Risk per trade (% of equity)", 0.25, 2.0, 1.0, 0.25)
    max_alloc = st.slider("Max size per coin (% of equity)", 5, 50, 25, 5)
    auto = st.toggle("Auto-refresh (20s)", value=True)

p = dict(rsi_entry=rsi_entry, rsi_exit=rsi_exit, atr_mult=atr_mult, rr=rr,
         ema_len=int(ema_len), max_pos=max_pos, risk_pct=risk_pct, max_alloc=max_alloc)

if "state" not in st.session_state:
    st.session_state.state = load_state()

st.warning("Paper trading only. Nothing here is financial advice, and past results "
           "do not guarantee future results.")

if not symbols:
    st.info("Pick at least one coin from the sidebar.")
    st.stop()

tab_live, tab_bt = st.tabs(["🔴 Live (Paper)", "🧪 Backtest"])


@st.fragment(run_every="20s" if auto else None)
def live_panel():
    state = st.session_state.state
    fetch_list = sorted(set(symbols) | set(state["positions"]))
    data, errs = fetch_all(fetch_list, interval)
    if errs:
        st.warning("Could not load: " + " | ".join(f"{k}: {v[:70]}" for k, v in errs.items()))
    data = {s: d for s, d in data.items() if len(d) > p["ema_len"] + 5}
    if not data:
        st.error("No market data available. Check your internet connection.")
        return
    data = {s: add_indicators(d, p["ema_len"]) for s, d in data.items()}

    prices, equity = live_step(data, p, state, set(symbols))
    save_state(state)

    trades = pd.DataFrame(state["trades"])
    total_pnl = equity - START_CASH
    n = len(trades)
    wr = (trades["pnl"] > 0).mean() * 100 if n else 0

    c = st.columns(5)
    c[0].metric("Equity", f"${equity:,.2f}", f"{total_pnl / START_CASH * 100:+.2f}%")
    c[1].metric("Cumulative P&L", f"${total_pnl:+,.2f}")
    c[2].metric("Cash", f"${state['cash']:,.2f}")
    c[3].metric("Open positions", f"{len(state['positions'])}/{p['max_pos']}")
    c[4].metric("Win rate", f"{wr:.0f}%" if n else "-", f"{n} closed trades")
    st.caption(f"Last update {time.strftime('%H:%M:%S')} · {len(data)} coins scanned "
               f"at the same time · {interval} candles")

    st.subheader("💹 Cumulative equity")
    hist = state["equity_hist"]
    if len(hist) >= 2:
        h = pd.DataFrame(hist, columns=["t", "Equity"])
        h["t"] = pd.to_datetime(h["t"], unit="s")
        st.line_chart(h.set_index("t"))
    else:
        st.info("The equity curve appears after a couple of minutes of running.")

    st.subheader("🔎 Market scanner")
    rows = []
    for sym, df in data.items():
        last, prev = df.iloc[-2], df.iloc[-3]
        up = last["close"] > last["ema"]
        if sym in state["positions"]:
            sig = "🟢 In trade"
        elif sym not in symbols:
            sig = "—"
        elif entry_signal(last, prev["rsi"], p):
            sig = "🔔 Buy signal"
        elif up and last["rsi"] < p["rsi_entry"]:
            sig = "👀 Watching (RSI low)"
        elif not up:
            sig = "⛔ Downtrend"
        else:
            sig = "—"
        rows.append({"Coin": sym.replace("USDT", ""), "Price": fmt_price(prices[sym]),
                     "RSI": round(float(last["rsi"]), 1), "Trend": "Up ✅" if up else "Down",
                     "vs EMA %": round((last["close"] / last["ema"] - 1) * 100, 2), "Signal": sig})
    show_df(pd.DataFrame(rows))

    st.subheader("📌 Open positions")
    if state["positions"]:
        rows = []
        for sym, pos in state["positions"].items():
            px = prices.get(sym, pos["entry"])
            pnl = pos["qty"] * px * (1 - FEE) - pos["cost"]
            rows.append({"Coin": sym.replace("USDT", ""), "Entry": fmt_price(pos["entry"]),
                         "Now": fmt_price(px), "Stop": fmt_price(pos["stop"]),
                         "Target": fmt_price(pos["tp"]), "P&L $": round(pnl, 2),
                         "P&L %": round(pnl / pos["cost"] * 100, 2)})
        show_df(pd.DataFrame(rows))
    else:
        st.write("No open positions. Waiting for valid signals.")

    st.subheader("📜 Closed trades")
    if n:
        trades["cum_pnl"] = trades["pnl"].cumsum().round(2)
        st.line_chart(trades["cum_pnl"], height=200)
        show_df(trades.iloc[::-1])
    else:
        st.write("No closed trades yet.")

    if st.button("🗑️ Reset paper account"):
        st.session_state.state = default_state()
        save_state(st.session_state.state)
        st.rerun(scope="fragment")


with tab_live:
    live_panel()


with tab_bt:
    st.write("Tests the same rules on the last 1000 candles of every selected coin "
             "(each coin starts with $1000, fees included).")
    if st.button("▶️ Run backtest"):
        with st.spinner("Downloading history and simulating..."):
            data, errs = fetch_all(symbols, interval, limit=1000, ttl=0)
            rows, eqs, bhs, all_tr = [], [], [], []
            for sym, df in data.items():
                h = add_indicators(df, p["ema_len"])
                tr, eq = backtest(h, p)
                if eq.empty:
                    continue
                s = stats(tr, eq)
                start = max(p["ema_len"], 30)
                close = h.set_index("time")["close"].iloc[start:]
                bh = close / close.iloc[0] * START_CASH
                eqs.append(eq.rename(sym))
                bhs.append(bh.rename(sym))
                if len(tr):
                    all_tr.append(tr)
                rows.append({"Coin": sym.replace("USDT", ""), "Return %": round(s["ret"], 1),
                             "Buy&Hold %": round((bh.iloc[-1] / START_CASH - 1) * 100, 1),
                             "Max DD %": round(s["dd"], 1), "Trades": s["trades"],
                             "Win rate %": round(s["win_rate"]),
                             "Profit factor": round(s["pf"], 2) if s["pf"] is not None else None})
        if errs:
            st.warning("Could not load: " + ", ".join(errs))
        if not rows:
            st.info("No data or no trades for these settings.")
        else:
            port = pd.concat(eqs, axis=1).ffill().mean(axis=1)
            bh_port = pd.concat(bhs, axis=1).ffill().mean(axis=1)
            tr_all = pd.concat(all_tr) if all_tr else pd.DataFrame()
            ps = stats(tr_all, port)
            m = st.columns(4)
            m[0].metric("Strategy (equal split)", f"{ps['ret']:+.1f}%")
            m[1].metric("Buy & Hold", f"{(bh_port.iloc[-1] / START_CASH - 1) * 100:+.1f}%")
            m[2].metric("Max drawdown", f"{ps['dd']:.1f}%")
            m[3].metric("Win rate", f"{ps['win_rate']:.0f}%", f"{ps['trades']} trades")
            st.line_chart(pd.DataFrame({"Strategy": port, "Buy & Hold": bh_port}))
            show_df(pd.DataFrame(rows))
            st.caption("Past performance does not predict future results. If the strategy "
                       "is weaker than Buy & Hold or the drawdown is large, do not rely on it.")
