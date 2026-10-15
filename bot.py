import streamlit as st
import pandas as pd
import requests

st.set_page_config(page_title="Pro Multi-Market Crypto Bot", layout="wide", page_icon="🚀")

# ---------------------------------------------------------
# 1. جلب قائمة العملات (أشهر عملات السوق لتجنب أي تعليق)
# ---------------------------------------------------------
@st.cache_data(ttl=300)
def get_top_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "ADAUSDT", "DOGEUSDT"]

# ---------------------------------------------------------
# 2. جلب الشمعات وحساب المؤشرات لعملة محددة
# ---------------------------------------------------------
def analyze_symbol(symbol):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100"
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
        
        # مؤشرات سريعة
        df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR
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
max_pos = st.sidebar.slider("أقصى عدد صفقات مفتوحة", 1, 4, 2)

if st.sidebar.button("🔄 إعادة ضبط المحفظة"):
    st.session_state.cash = 1000.0
    st.session_state.open_positions = {}
    st.session_state.trade_history = []
    st.rerun()

st.title("🚀 Pro Crypto Multi-Market Bot")
st.markdown("لوحة تحكم ذكية تمسح أهم أسواق العملات الرقمية وتنفذ صفقات بربح تراكمي.")

symbols = get_top_symbols()

# حساب إجمالي قيمة المحفظة (الرصيد النقدي + الصفقات المفتوحة)
total_equity = st.session_state.cash
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        total_equity += pos['qty'] * latest['close']

# عرض المؤشرات العليا الرئيسية
col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المحفظة (Equity)", f"${total_equity:,.2f}")
col2.metric("الكاش المتاح", f"${st.session_state.cash:,.2f}")
col3.metric("الصفقات النشطة", len(st.session_state.open_positions))
col4.metric("الصفقات المكتملة", len(st.session_state.trade_history))

st.markdown("---")

# ---------------------------------------------------------
# 5. فحص السوق وإدارة الصفقات
# ---------------------------------------------------------
signals = []
for sym in symbols:
    latest, prev = analyze_symbol(sym)
    if latest is not None and prev is not None:
        price = latest['close']
        ema = latest['EMA_20']
        rsi = latest['RSI']
        atr = latest['ATR']
        
        # إشارة شراء ذكية وسريعة
        if (price > ema) and (prev['RSI'] <= 45 and rsi > 45):
            signals.append({'symbol': sym, 'price': price, 'atr': atr})

# تحديث وإغلاق الصفقات المفتوحة عند تحقيق الأهداف
for sym, pos in list(st.session_state.open_positions.items()):
    latest, _ = analyze_symbol(sym)
    if latest is not None:
        curr_price = latest['close']
        if curr_price <= pos['sl'] or curr_price >= pos['tp'] or latest['RSI'] >= 75:
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

# فتح صفقات جديدة إذا توفر كاش
for sig in signals:
    sym = sig['symbol']
    if sym not in st.session_state.open_positions and len(st.session_state.open_positions) < max_pos:
        if st.session_state.cash > 50:
            allocation = st.session_state.cash * 0.4  # استثمار 40% من الكاش المتاح بكل صفقة
            st.session_state.cash -= allocation
            qty = allocation / sig['price']
            sl = sig['price'] - (1.5 * sig['atr'])
            tp = sig['price'] + (2.5 * sig['atr'])
            
            st.session_state.open_positions[sym] = {
                'entry': sig['price'],
                'qty': qty,
                'sl': sl,
                'tp': tp
            }

# ---------------------------------------------------------
# 6. عرض الجداول والبيانات بوضوح تام
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
    st.info("🔍 البوت يبحث في الأسواق عن إشارات دخول مناسبة...")

if st.session_state.trade_history:
    st.subheader("📜 سجل الصفقات المغلقة (أرباح تراكمية)")
    st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True)

# زر تحديث يدوي لتجنب تكرار الشاشة البيضاء وضمان ثبات الواجهة
if st.button("🔄 تحديث السوق والصفقات الآن"):
    st.rerun()
