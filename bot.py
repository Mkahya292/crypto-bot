import streamlit as st
import pandas as pd
import requests
import time

st.set_page_config(page_title="Multi-Strategy Crypto Bot", layout="wide", page_icon="📈")

# ---------------------------------------------------------
# 1. جلب البيانات من Binance
# ---------------------------------------------------------
@st.cache_data(ttl=10)
def get_klines(symbol="BTCUSDT", interval="1m", limit=300):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['open'] = df['open'].astype(float)
        return df
    except Exception as e:
        st.error(f"خطأ في سحب البيانات: {e}")
        return pd.DataFrame()

# ---------------------------------------------------------
# 2. حساب المؤشرات الفنية لكافة الاستراتيجيات
# ---------------------------------------------------------
def calculate_indicators(df):
    if df.empty:
        return df
    
    # متوسطات متحركة لتقاطع المتوسطات
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands (20, 2)
    df['BB_Mid'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Mid'] + (bb_std * 2)
    df['BB_Lower'] = df['BB_Mid'] - (bb_std * 2)
    
    # ATR (14) لإدارة المخاطر
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(14).mean()
    
    return df

# ---------------------------------------------------------
# 3. إدارة جلسة المحاكاة
# ---------------------------------------------------------
if 'cash' not in st.session_state:
    st.session_state.cash = 1000.0
if 'position' not in st.session_state:
    st.session_state.position = None
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []

# ---------------------------------------------------------
# 4. الشريط الجانبي (الإعدادات واختيار الاستراتيجية)
# ---------------------------------------------------------
st.sidebar.title("⚙️ لوحة تحكم الاستراتيجيات")
symbol = st.sidebar.selectbox("الزوج", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], index=0)
timeframe = st.sidebar.selectbox("الإطار الزمني", ["1m", "3m", "5m", "15m"], index=0)

# اختيار الاستراتيجية من القائمة
strategy_choice = st.sidebar.selectbox(
    "اختر استراتيجية التداول",
    [
        "1. تقاطع المتوسطات السريعة (EMA 9/21)",
        "2. التشبع البيعي لـ RSI (< 30)",
        "3. ارتداد البولنجر باند السفلي"
    ]
)

auto_refresh = st.sidebar.checkbox("تحديث تلقائي كل 10 ثوانٍ", value=True)

if st.sidebar.button("إعادة ضبط حساب المحاكاة"):
    st.session_state.cash = 1000.0
    st.session_state.position = None
    st.session_state.trade_history = []
    st.rerun()

# ---------------------------------------------------------
# 5. تنفيذ منطق التداول حسب الاستراتيجية المختارة
# ---------------------------------------------------------
df = get_klines(symbol=symbol, interval=timeframe, limit=300)
df = calculate_indicators(df)

if not df.empty:
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    current_price = latest['close']
    atr = latest['ATR']
    rsi = latest['RSI']
    
    equity = st.session_state.cash
    if st.session_state.position:
        equity = st.session_state.position['qty'] * current_price

    st.title(f"🚀 Multi-Strategy Bot [{timeframe}] - {symbol}")
    st.markdown(f"**الاستراتيجية النشطة حالياً:** `{strategy_choice}`")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("الرصيد النقدي", f"${st.session_state.cash:,.2f}")
    col2.metric("السعر الحالي", f"${current_price:,.2f}")
    col3.metric("مؤشر RSI", f"{rsi:.1f}")
    col4.metric("إجمالي المحفظة", f"${equity:,.2f}")

    st.markdown("---")

    # تحديد إشارة الشراء بناءً على الاستراتيجية المختارة
    buy_signal = False
    exit_signal = False
    exit_reason_custom = ""

    if "1." in strategy_choice:
        # تقاطع المتوسطات: EMA 9 يتقاطع صعوداً فوق EMA 21
        buy_signal = (prev['EMA_9'] <= prev['EMA_21']) and (latest['EMA_9'] > latest['EMA_21'])
        exit_signal = (prev['EMA_9'] >= prev['EMA_21']) and (latest['EMA_9'] < latest['EMA_21'])
        exit_reason_custom = "EMA Cross Down 📉"

    elif "2." in strategy_choice:
        # RSI Oversold: خروج من منطقة تحت 30 للصعود فوقها
        buy_signal = (prev['RSI'] <= 30) and (latest['RSI'] > 30)
        exit_signal = latest['RSI'] >= 70
        exit_reason_custom = "RSI Overbought 📈"

    elif "3." in strategy_choice:
        # Bollinger Bands Bounce: ملامسة السعر للخط السفلي والارتداد للداخل
        buy_signal = (prev['close'] <= prev['BB_Lower']) and (latest['close'] > latest['BB_Lower'])
        exit_signal = latest['close'] >= latest['BB_Mid']
        exit_reason_custom = "BB Midline Target 🎯"

    # فتح الصفقة إذا لم تكن موجودة وجاءت إشارة الشراء
    if st.session_state.position is None:
        if buy_signal:
            entry_price = current_price
            sl = entry_price - (1.5 * atr)
            tp = entry_price + (2.5 * atr)
            qty = (st.session_state.cash * 0.99) / entry_price
            
            st.session_state.position = {
                'entry_price': entry_price,
                'qty': qty,
                'sl': sl,
                'tp': tp,
                'time': pd.Timestamp.now().strftime('%H:%M:%S')
            }
            st.session_state.cash -= (qty * entry_price)
            st.success(f"🚀 تم فتح صفقة جديدة بناءً على الاستراتيجية بسعر: ${entry_price:,.2f}")
            st.rerun()

    # متابعة الصفقة المفتوحة
    else:
        pos = st.session_state.position
        pnl = (current_price - pos['entry_price']) * pos['qty']
        pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
        
        st.subheader("📌 الصفقة المفتوحة حالياً")
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"**سعر الدخول:** ${pos['entry_price']:,.2f}")
        c2.write(f"**وقف الخسارة (SL):** ${pos['sl']:,.2f}")
        c3.write(f"**أخذ الأرباح (TP):** ${pos['tp']:,.2f}")
        c4.write(f"**الربح/الخسارة:** :{'green' if pnl>=0 else 'red'}[${pnl:,.2f} ({pnl_pct:.2f}%)]")

        hit_sl = current_price <= pos['sl']
        hit_tp = current_price >= pos['tp']
        
        if hit_sl or hit_tp or exit_signal:
            reason = "Stop Loss 🛑" if hit_sl else ("Take Profit 🎯" if hit_tp else exit_reason_custom)
            exit_price = current_price
            return_cash = pos['qty'] * exit_price
            
            st.session_state.cash += return_cash
            st.session_state.trade_history.append({
                'entry': pos['entry_price'],
                'exit': exit_price,
                'pnl': (exit_price - pos['entry_price']) * pos['qty'],
                'reason': reason,
                'time': pd.Timestamp.now().strftime('%H:%M:%S')
            })
            st.session_state.position = None
            st.info(f"تم إغلاق الصفقة السبب: {reason} بسعر ${exit_price:,.2f}")
            st.rerun()

    # عرض الرسم البياني المناسب حسب الاستراتيجية
    st.subheader(f"📈 الرسوم البيانية والمؤشرات ({timeframe})")
    if "1." in strategy_choice:
        st.line_chart(df[['close', 'EMA_9', 'EMA_21']].tail(100))
    elif "2." in strategy_choice:
        st.line_chart(df[['close']].tail(100))
        st.caption("مؤشر RSI الحالي: " + str(round(rsi, 2)))
    else:
        st.line_chart(df[['close', 'BB_Upper', 'BB_Mid', 'BB_Lower']].tail(100))

    if st.session_state.trade_history:
        st.subheader("📜 سجل الصفقات المكتملة")
        st.dataframe(pd.DataFrame(st.session_state.trade_history))

if auto_refresh:
    time.sleep(10)
    st.rerun()
