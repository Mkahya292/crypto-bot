"""
Pro Crypto Paper Trading Bot  -  وضع محاكاة فقط (بدون مال حقيقي وبدون مفاتيح API)

الاستراتيجية:
  - فلتر اتجاه: السعر فوق EMA 200
  - دخول: RSI يعبر لأعلى مستوى الدخول (ارتداد بعد تشبع بيعي)
  - وقف خسارة = ATR × معامل، جني أرباح = المخاطرة × نسبة العائد/المخاطرة
  - خروج إضافي: RSI فوق مستوى الخروج
  - حجم الصفقة = نسبة مخاطرة ثابتة من الرصيد، بدون رافعة
  - رسوم 0.1% على كل عملية شراء وبيع

التشغيل:
  pip install -U streamlit pandas requests
  streamlit run bot.py
"""
import json
import os

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Pro Crypto Paper Bot", page_icon="📈", layout="wide")

STATE_FILE = "paper_state.json"
BASES = ["https://api.binance.com", "https://data-api.binance.vision"]
FEE = 0.001
START_CASH = 1000.0


# ----------------------------- البيانات -----------------------------
@st.cache_data(ttl=20)
def get_klines(symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    last_err = None
    for base in BASES:
        try:
            r = requests.get(
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
            return df
        except Exception as e:  # جرّب المصدر التالي
            last_err = e
    raise RuntimeError(f"تعذر جلب البيانات من Binance: {last_err}")


def add_indicators(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
    df["ema"] = df["close"].ewm(span=p["ema_len"], adjust=False).mean()
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df


def entry_signal(row, prev_rsi, p) -> bool:
    return bool(row["close"] > row["ema"] and prev_rsi < p["rsi_entry"] <= row["rsi"])


def position_size(equity, cash, price, atr, p):
    stop_dist = atr * p["atr_mult"]
    if stop_dist <= 0:
        return 0.0
    qty = (equity * p["risk_pct"] / 100) / stop_dist
    max_qty = cash / (price * (1 + FEE))  # بدون رافعة
    return max(0.0, min(qty, max_qty))


# ----------------------------- الحالة (محاكاة) -----------------------------
def default_state():
    return dict(cash=START_CASH, qty=0.0, entry=0.0, stop=0.0, tp=0.0, cost=0.0,
                entry_time="", last_candle="", trades=[])


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return {**default_state(), **json.load(f)}
        except Exception:
            pass
    return default_state()


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def close_position(state, price, reason, when):
    proceeds = state["qty"] * price * (1 - FEE)
    pnl = proceeds - state["cost"]
    state["cash"] += proceeds
    state["trades"].append(dict(
        entry_time=state["entry_time"], exit_time=str(when), entry=state["entry"],
        exit=price, pnl=round(pnl, 2), pnl_pct=round(pnl / state["cost"] * 100, 2), reason=reason))
    state.update(qty=0.0, entry=0.0, stop=0.0, tp=0.0, cost=0.0, entry_time="")


def live_step(df, p, state):
    closed = df.iloc[:-1]  # آخر شمعة مغلقة فقط للإشارات
    price = float(df.iloc[-1]["close"])
    now = df.iloc[-1]["time"]
    last, prev = closed.iloc[-1], closed.iloc[-2]

    if state["qty"] > 0:
        reason = None
        if price <= state["stop"]:
            reason = "Stop Loss"
        elif price >= state["tp"]:
            reason = "Take Profit"
        elif last["rsi"] > p["rsi_exit"]:
            reason = "RSI Exit"
        if reason:
            close_position(state, price, reason, now)

    candle_id = str(last["time"])
    if state["qty"] == 0 and state["last_candle"] != candle_id and entry_signal(last, prev["rsi"], p):
        equity = state["cash"]
        qty = position_size(equity, state["cash"], price, last["atr"], p)
        if qty * price > 10:  # حد أدنى للصفقة
            risk = last["atr"] * p["atr_mult"]
            state.update(qty=qty, entry=price, stop=price - risk, tp=price + risk * p["rr"],
                         cost=qty * price * (1 + FEE), entry_time=str(now))
            state["cash"] -= state["cost"]
    state["last_candle"] = candle_id
    return price


# ----------------------------- اختبار تاريخي -----------------------------
def backtest(df, p):
    cash, qty, entry, stop, tp, cost = START_CASH, 0.0, 0.0, 0.0, 0.0, 0.0
    trades, equity, times = [], [], []
    start = max(p["ema_len"], 30)
    for i in range(start, len(df)):
        row, prev = df.iloc[i], df.iloc[i - 1]
        if qty > 0:
            exit_price, reason = None, None
            if row["low"] <= stop:
                exit_price, reason = min(stop, row["open"]), "Stop Loss"
            elif row["high"] >= tp:
                exit_price, reason = max(tp, row["open"]), "Take Profit"
            elif row["rsi"] > p["rsi_exit"]:
                exit_price, reason = row["close"], "RSI Exit"
            if exit_price is not None:
                proceeds = qty * exit_price * (1 - FEE)
                pnl = proceeds - cost
                cash += proceeds
                trades.append(dict(time=row["time"], pnl=pnl, pnl_pct=pnl / cost * 100, reason=reason))
                qty = 0.0
        if qty == 0 and entry_signal(row, prev["rsi"], p):
            q = position_size(cash, cash, row["close"], row["atr"], p)
            if q * row["close"] > 10:
                risk = row["atr"] * p["atr_mult"]
                qty, entry = q, row["close"]
                stop, tp = entry - risk, entry + risk * p["rr"]
                cost = qty * entry * (1 + FEE)
                cash -= cost
        equity.append(cash + qty * row["close"])
        times.append(row["time"])
    return pd.DataFrame(trades), pd.Series(equity, index=times)


# ----------------------------- الواجهة -----------------------------
st.title("🚀 Pro Crypto Paper Trading Bot")
st.warning("وضع محاكاة (Paper Trading) فقط: لا يوجد اتصال بأي حساب حقيقي ولا مفاتيح API.")

with st.sidebar:
    st.header("⚙️ Settings")
    symbol = st.selectbox("Symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    interval = st.selectbox("Timeframe", ["15m", "1h", "4h"], index=1)
    rsi_entry = st.slider("RSI entry (cross up)", 20, 50, 40)
    rsi_exit = st.slider("RSI exit (above)", 55, 85, 70)
    atr_mult = st.slider("Stop loss (ATR x)", 1.0, 4.0, 2.0, 0.5)
    rr = st.slider("Take profit (Risk:Reward)", 1.0, 4.0, 2.0, 0.5)
    risk_pct = st.slider("Risk per trade (% of equity)", 0.25, 2.0, 1.0, 0.25)
    ema_len = st.number_input("Trend EMA length", 50, 300, 200, 10)

params = dict(symbol=symbol, interval=interval, rsi_entry=rsi_entry, rsi_exit=rsi_exit,
              atr_mult=atr_mult, rr=rr, risk_pct=risk_pct, ema_len=int(ema_len))

if "state" not in st.session_state:
    st.session_state.state = load_state()

tab_live, tab_bt = st.tabs(["🔴 Live (Paper)", "🧪 Backtest"])


@st.fragment(run_every="30s")
def live_panel(p):
    try:
        df = add_indicators(get_klines(p["symbol"], p["interval"], 500), p)
    except Exception as e:
        st.error(str(e))
        return
    state = st.session_state.state
    price = live_step(df, p, state)
    save_state(state)

    equity = state["cash"] + state["qty"] * price
    pnl_pct = (equity - START_CASH) / START_CASH * 100
    last = df.iloc[-2]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Cash", f"${state['cash']:,.2f}")
    c2.metric(f"📈 {p['symbol']}", f"${price:,.2f}")
    c3.metric("🤖 Status", "IN TRADE 🟢" if state["qty"] > 0 else "WAITING ⏳")
    c4.metric("📊 Equity", f"${equity:,.2f}", delta=f"{pnl_pct:+.2f}%")

    trend = "Uptrend ✅" if last["close"] > last["ema"] else "Downtrend ⛔ (no entries)"
    st.caption(f"RSI: {last['rsi']:.1f}  |  Trend: {trend}  |  ATR: {last['atr']:,.2f}")

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Price vs EMA")
        st.line_chart(df.set_index("time")[["close", "ema"]].tail(200))
    with right:
        st.subheader("Position")
        if state["qty"] > 0:
            st.info(f"Holding {state['qty']:.6f}\n\nEntry: ${state['entry']:,.2f}\n\n"
                    f"Stop: ${state['stop']:,.2f}\n\nTarget: ${state['tp']:,.2f}")
        else:
            st.success("Waiting for a valid entry signal...")
        if st.button("🗑️ Reset paper account"):
            st.session_state.state = default_state()
            save_state(st.session_state.state)
            st.rerun(scope="fragment")

    st.subheader("Trades log")
    if state["trades"]:
        t = pd.DataFrame(state["trades"])
        wins = (t["pnl"] > 0).sum()
        st.caption(f"Trades: {len(t)}  |  Win rate: {wins / len(t) * 100:.0f}%  |  Net PnL: ${t['pnl'].sum():,.2f}")
        st.dataframe(t.iloc[::-1], use_container_width=True)
    else:
        st.write("No closed trades yet.")


with tab_live:
    live_panel(params)

with tab_bt:
    st.write("اختبار الاستراتيجية على آخر 1000 شمعة بنفس الإعدادات (رسوم 0.1% محسوبة).")
    if st.button("▶️ Run backtest"):
        try:
            hist = add_indicators(get_klines(symbol, interval, 1000), params)
            trades, eq = backtest(hist, params)
        except Exception as e:
            st.error(str(e))
        else:
            bh = (hist["close"].iloc[-1] / hist["close"].iloc[params["ema_len"]] - 1) * 100
            ret = (eq.iloc[-1] / START_CASH - 1) * 100
            dd = ((eq / eq.cummax()) - 1).min() * 100
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Strategy return", f"{ret:+.1f}%")
            m2.metric("Buy & Hold", f"{bh:+.1f}%")
            m3.metric("Max drawdown", f"{dd:.1f}%")
            if len(trades):
                wins = trades[trades["pnl"] > 0]["pnl"].sum()
                losses = -trades[trades["pnl"] <= 0]["pnl"].sum()
                m4.metric("Win rate", f"{(trades['pnl'] > 0).mean() * 100:.0f}% ({len(trades)} trades)")
                st.caption(f"Profit factor: {wins / losses:.2f}" if losses > 0 else "Profit factor: no losing trades")
                st.dataframe(trades, use_container_width=True)
            else:
                m4.metric("Trades", "0")
                st.info("ما صار ولا إشارة بهالفترة بهالإعدادات.")
            st.line_chart(eq)
            st.caption("نتيجة الماضي ما بتضمن المستقبل. إذا خسرت الاستراتيجية أو ضعف أداؤها عن Buy & Hold، لا تعتمد عليها.")
