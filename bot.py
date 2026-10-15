import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="Pro Multi-Market Crypto Bot", layout="wide", page_icon="🚀")

# ---------------------------------------------------------
# 1. جلب قائمة العملات الرئيسية
# ---------------------------------------------------------
@st.cache_data(ttl=300)
def get_top_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "ADAUSDT", "DOGEUSDT"]

# ---------------------------------------------------------
# 2. جلب الشمعات وحساب المؤشرات السريعة
# ---------------------------------------------------------
def analyze_symbol(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=100"
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        
        # مؤشرات أسرع (EMA 10)
        df['EMA_10'] = df['close'].ewm(span=10, adjust=False).mean()
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR لإدارة المخاطر
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        df['ATR'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(14).mean()
        
        return df.iloc[-1], df.iloc[-2]
    except:
        return None, None

# ---------------------------------------------------------
# 3. إدارة جلسة المحاكاة والربح التراكمي
# ---------------------------------------------------------
if 'cash' not in st.session_state:
    st.session_state.cash = 1000.0
if 'open_positions' not in st.session_state:
    st.session_state.open_positions = {}
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []

# ---------------------------------------------------------
# 4. لوحة التحكم الجانبية
# ---------------------------------------------------------
st.sidebar.title("⚡ إعدادات الماسح الشامل")
max_pos = st.sidebar.slider("أقصى عدد صفقات مفتوحة معاً", 1, 5, 3)

if st.sidebar.button("🔄 إعادة ضبط المحفظة"):
    st.session_state.cash = 1000.0
    st.session_state.open_positions = {}
    st.session_state.trade_history = []
    st.rerun()

st.title("🚀 Pro Crypto Multi-Market Bot (Fast Signals)")
st.markdown("ماسح ذكي لأسواق العملات الرقمية يقتنص الصفقات السريعة ويحقق أرباحاً تراكمية.")

symbols = get_top_symbols()

# حساب إجمالي قيمة المحفظة
total_equity = st.session_state.cash
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        total_equity += pos['qty'] * latest['close']

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المحفظة (Equity)", f"${total_equity:,.2f}")
col2.metric("الكاش المتاح", f"${st.session_state.cash:,.2f}")
col3.metric("الصفقات النشطة", len(st.session_state.open_positions))
col4.metric("الصفقات المكتملة", len(st.session_state.trade_history))

st.markdown("---")

# ---------------------------------------------------------
# 5. فحص السوق بشروط أسرع وأكثر مرونة
# ---------------------------------------------------------
signals = []
for sym in symbols:
    latest, prev = analyze_symbol(sym)
    if latest is not None and prev is not None:
        price = latest['close']
        ema = latest['EMA_10']
        rsi = latest['RSI']
        atr = latest['ATR']
        
        # شرط دخول سريع جداً (RSI فوق 40 صعوداً والسعر فوق EMA 10)
        if (price > ema) and (rsi > 40 and rsi < 65):
            signals.append({'symbol': sym, 'price': price, 'atr': atr})

# إدارة وإغلاق الصفقات المفتوحة
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        curr_price = latest['close']
        if curr_price <= pos['sl'] or curr_price >= pos['tp'] or latest['RSI'] >= 70:
            exit_cash = pos['qty'] * curr_price
            pnl = exit_cash - (pos['qty'] * pos['entry'])
            st.session_state.cash += exit_cash
            st.session_state.trade_history.append({
                'العملة': sym,
                'الدخول': f"${pos['entry']:,.2f}",
                'الخروج': f"${curr_price:,.2f}",
                'الربح/الخسارة': f"${pnl:,.2f}",
                'الوقت': pd.Timestamp.now().strftime('%H:%M:%S')
            })
            del st.session_state.open_positions[sym]

# فتح صفقات جديدة بسرعة بناءً على الإشارات المتاحة
for sig in signals:
    sym = sig['symbol']
    if sym not in st.session_state.open_positions and len(st.session_state.open_positions) < max_pos:
        if st.session_state.cash > 50:
            allocation = st.session_state.cash * 0.35  # تخصيص 35% من الكاش لكل صفقة
            st.session_state.cash -= allocation
            qty = allocation / sig['price']
            sl = sig['price'] - (1.2 * sig['atr'])
            tp = sig['price'] + (2.0 * sig['atr'])
            
            st.session_state.open_positions[sym] = {
                'entry': sig['price'],
                'qty': qty,
                'sl': sl,
                'tp': tp
            }

# ---------------------------------------------------------
# 6. عرض الجداول والتحديث
# ---------------------------------------------------------
st.subheader("📌 الصفقات المفتوحة حالياً")
if st.session_state.open_positions:
    open_list = []
    for sym, pos in st.session_state.open_positions.items():
        latest, _ = analyze_symbol(sym)
        curr_p = latest['close'] if latest is not None else pos['entry']
        pnl_val = (curr_p - pos['entry']) * pos['qty']
        open_list.append({
            'العملة': sym,
            'سعر الدخول': f"${pos['entry']:,.2f}",
            'السعر الحالي': f"${curr_p:,.2f}",
            'الربح الحالي': f"${pnl_val:,.2f}"
        })
    st.dataframe(pd.DataFrame(open_list), use_container_width=True)
else:
    st.info("⚡ اضغط على زر التحديث أدناه لاصطياد صفقات جديدة فوراً...")

if st.session_state.trade_history:
    st.subheader("📜 سجل الصفقات المغلقة (أرباح تراكمية)")
    st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True)

if st.button("🔄 تحديث السوق والصفقات الآن"):
    st.rerun()
import streamlit as st
import pandas as pd
import requests
import time

st.set_page_config(page_title="Auto Instant Crypto Bot", layout="wide", page_icon="⚡")

@st.cache_data(ttl=60)
def get_top_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "ADAUSDT", "DOGEUSDT"]

def analyze_symbol(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=50"
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['open'] = df['open'].astype(float)
        
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        df['ATR'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(14).mean()
        
        return df.iloc[-1], df.iloc[-2]
    except:
        return None, None

if 'cash' not in st.session_state:
    st.session_state.cash = 1000.0
if 'open_positions' not in st.session_state:
    st.session_state.open_positions = {}
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []

st.sidebar.title("⚡ إعدادات التشغيل التلقائي")
max_pos = st.sidebar.slider("أقصى عدد صفقات مفتوحة معاً", 1, 6, 4)
auto_refresh = st.sidebar.checkbox("تحديث آلي مستمر", value=True)

if st.sidebar.button("🔄 إعادة ضبط المحفظة"):
    st.session_state.cash = 1000.0
    st.session_state.open_positions = {}
    st.session_state.trade_history = []
    st.rerun()

st.title("⚡ Fully Autonomous Crypto Bot")
st.markdown("البوت يعمل تلقائياً بالكامل في الخلفية: يمسح الأسواق، يفتح الصفقات، ويراقب الأرباح التراكمية ذاتياً.")

symbols = get_top_symbols()

total_equity = st.session_state.cash
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        total_equity += pos['qty'] * latest['close']

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المحفظة (Equity)", f"${total_equity:,.2f}")
col2.metric("الكاش المتاح", f"${st.session_state.cash:,.2f}")
col3.metric("الصفقات النشطة", len(st.session_state.open_positions))
col4.metric("الصفقات المكتملة", len(st.session_state.trade_history))

st.markdown("---")

# فحص السوق واقتناص الصفقات آلياً
signals = []
for sym in symbols:
    latest, prev = analyze_symbol(sym)
    if latest is not None:
        price = latest['close']
        atr = latest['ATR']
        is_green = latest['close'] > latest['open']
        
        if is_green:
            signals.append({'symbol': sym, 'price': price, 'atr': atr})

# إدارة إغلاق الصفقات تلقائياً
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        curr_price = latest['close']
        if curr_price <= pos['sl'] or curr_price >= pos['tp']:
            exit_cash = pos['qty'] * curr_price
            pnl = exit_cash - (pos['qty'] * pos['entry'])
            st.session_state.cash += exit_cash
            st.session_state.trade_history.append({
                'العملة': sym,
                'الدخول': f"${pos['entry']:,.2f}",
                'الخروج': f"${curr_price:,.2f}",
                'الربح/الخسارة': f"${pnl:,.2f}",
                'الوقت': pd.Timestamp.now().strftime('%H:%M:%S')
            })
            del st.session_state.open_positions[sym]

# فتح الصفقات الجديدة تلقائياً
for sig in signals:
    sym = sig['symbol']
    if sym not in st.session_state.open_positions and len(st.session_state.open_positions) < max_pos:
        if st.session_state.cash > 50:
            allocation = st.session_state.cash * 0.25
            st.session_state.cash -= allocation
            qty = allocation / sig['price']
            sl = sig['price'] - (1.0 * sig['atr'])
            tp = sig['price'] + (1.5 * sig['atr'])
            
            st.session_state.open_positions[sym] = {
                'entry': sig['price'],
                'qty': qty,
                'sl': sl,
                'tp': tp
            }

st.subheader("📌 الصفقات المفتوحة حالياً")
if st.session_state.open_positions:
    open_list = []
    for sym, pos in st.session_state.open_positions.items():
        latest, _ = analyze_symbol(sym)
        curr_p = latest['close'] if latest is not None else pos['entry']
        pnl_val = (curr_p - pos['entry']) * pos['qty']
        open_list.append({
            'العملة': sym,
            'سعر الدخول': f"${pos['entry']:,.2f}",
            'السعر الحالي': f"${curr_p:,.2f}",
            'الربح الحالي': f"${pnl_val:,.2f}"
        })
    st.dataframe(pd.DataFrame(open_list), use_container_width=True)
else:
    st.info("🤖 البوت يعمل آلياً في الخلفية ويقوم بالمسح وافتتاح الصفقات...")

if st.session_state.trade_history:
    st.subheader("📜 سجل الصفقات المغلقة (أرباح تراكمية)")
    st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True)

# حلقة التحديث الآلي في الخلفية
if auto_refresh:
    time.sleep(10)
    st.rerun()
