import streamlit as st
import pandas as pd
import requests
import time

st.set_page_config(page_title="Instant Autonomous Bot", layout="wide", page_icon="⚡")

@st.cache_data(ttl=30)
def get_top_symbols():
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

def get_current_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        res = requests.get(url, timeout=5)
        return float(res.json()['price'])
    except:
        return None

if 'cash' not in st.session_state:
    st.session_state.cash = 1000.0
if 'open_positions' not in st.session_state:
    st.session_state.open_positions = {}
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []

st.sidebar.title("⚡ إعدادات التشغيل الفوري")
max_pos = st.sidebar.slider("أقصى عدد صفقات مفتوحة معاً", 1, 5, 3)
auto_refresh = st.sidebar.checkbox("تحديث آلي مستمر", value=True)

if st.sidebar.button("🔄 إعادة ضبط المحفظة"):
    st.session_state.cash = 1000.0
    st.session_state.open_positions = {}
    st.session_state.trade_history = []
    st.rerun()

st.title("⚡ Instant Autonomous Crypto Bot")
st.markdown("البوت يقتنص الصفقات فوراً ويدير الأرباح التراكمية بشكل آلي بالكامل.")

symbols = get_top_symbols()

# حساب إجمالي قيمة المحفظة
total_equity = st.session_state.cash
for sym, pos in list(st.session_state.open_positions.items()):
    curr_p = get_current_price(sym)
    if curr_p:
        total_equity += pos['qty'] * curr_p

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المحفظة (Equity)", f"${total_equity:,.2f}")
col2.metric("الكاش المتاح", f"${st.session_state.cash:,.2f}")
col3.metric("الصفقات النشطة", len(st.session_state.open_positions))
col4.metric("الصفقات المكتملة", len(st.session_state.trade_history))

st.markdown("---")

# إدارة إغلاق الصفقات تلقائياً (بيع بربح بسيط أو وقف خسارة وهمي للتجربة الحية)
for sym, pos in list(st.session_state.open_positions.items()):
    curr_price = get_current_price(sym)
    if curr_price:
        pnl_pct = (curr_price - pos['entry']) / pos['entry']
        # إذا حققت ربحاً بنسبة 0.5% أو خسارة 0.5% يتم إغلاقها لتوليد أرباح تراكمية سريعة
        if pnl_pct >= 0.005 or pnl_pct <= -0.005:
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

# فتح صفقات جديدة فوراً إذا لم نصل للحد الأقصى
for sym in symbols:
    if sym not in st.session_state.open_positions and len(st.session_state.open_positions) < max_pos:
        if st.session_state.cash > 50:
            price = get_current_price(sym)
            if price:
                allocation = st.session_state.cash * 0.3  # تخصيص 30% من الكاش
                st.session_state.cash -= allocation
                qty = allocation / price
                sl = price * 0.99  # وقف خسارة قريب جداً
                tp = price * 1.015  # أخذ أرباح سريع
                
                st.session_state.open_positions[sym] = {
                    'entry': price,
                    'qty': qty,
                    'sl': sl,
                    'tp': tp
                }

st.subheader("📌 الصفقات المفتوحة حالياً")
if st.session_state.open_positions:
    open_list = []
    for sym, pos in st.session_state.open_positions.items():
        curr_p = get_current_price(sym) or pos['entry']
        pnl_val = (curr_p - pos['entry']) * pos['qty']
        open_list.append({
            'العملة': sym,
            'سعر الدخول': f"${pos['entry']:,.2f}",
            'السعر الحالي': f"${curr_p:,.2f}",
            'الربح الحالي': f"${pnl_val:,.2f}"
        })
    st.dataframe(pd.DataFrame(open_list), use_container_width=True)
else:
    st.info("⏳ جاري فتح الصفقات الفورية...")

if st.session_state.trade_history:
    st.subheader("📜 سجل الصفقات المغلقة (أرباح تراكمية)")
    st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True)

if auto_refresh:
    time.sleep(10)
    st.rerun()
