import streamlit as st
import pandas as pd
import requests
import time

st.set_page_config(page_title="Live Crypto Trading Bot", layout="wide", page_icon="📈")

# قائمة العملات الحقيقية
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# جلب السعر الحي الحقيقي حصرياً من Binance
def get_live_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            return float(res.json()['price'])
    except:
        pass
    return None

if 'cash' not in st.session_state:
    st.session_state.cash = 1000.0
if 'open_positions' not in st.session_state:
    st.session_state.open_positions = {}
if 'trade_history' not in st.session_state:
    st.session_state.trade_history = []

st.sidebar.title("⚙️ لوحة التحكم الحية")
max_pos = st.sidebar.slider("أقصى صفقات مفتوحة معاً", 1, 5, 3)
auto_refresh = st.sidebar.checkbox("تحديث آلي مستمر", value=True)

if st.sidebar.button("🔄 إعادة ضبط المحفظة"):
    st.session_state.cash = 1000.0
    st.session_state.open_positions = {}
    st.session_state.trade_history = []
    st.rerun()

st.title("📈 Live Market Crypto Bot (Real Data)")
st.markdown("البوت متصل مباشرة بسيرفرات Binance لجلب الأسعار الحقيقية، فتح الصفقات، وتحقيق الأرباح التراكمية.")

# حساب إجمالي قيمة المحفظة بالأسعار الحية
total_equity = st.session_state.cash
current_live_prices = {}

for sym in SYMBOLS:
    price = get_live_price(sym)
    if price:
        current_live_prices[sym] = price

for sym, pos in list(st.session_state.open_positions.items()):
    if sym in current_live_prices:
        total_equity += pos['qty'] * current_live_prices[sym]
    else:
        total_equity += pos['qty'] * pos['entry']

col1, col2, col3, col4 = st.columns(4)
col1.metric("إجمالي المحفظة (Equity)", f"${total_equity:,.2f}")
col2.metric("الكاش المتاح", f"${st.session_state.cash:,.2f}")
col3.metric("الصفقات النشطة", len(st.session_state.open_positions))
col4.metric("الصفقات المكتملة", len(st.session_state.trade_history))

st.markdown("---")

# إدارة إغلاق الصفقات الحية (إذا تحرك السعر بنسبة ربح 0.4% أو خسارة 0.4%)
for sym, pos in list(st.session_state.open_positions.items()):
    if sym in current_live_prices:
        curr_price = current_live_prices[sym]
        pnl_pct = (curr_price - pos['entry']) / pos['entry']
        
        if pnl_pct >= 0.004 or pnl_pct <= -0.004:
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

# فتح صفقات حية جديدة فوراً إذا توفر السعر الحقيقي والكاش
for sym, price in current_live_prices.items():
    if sym not in st.session_state.open_positions and len(st.session_state.open_positions) < max_pos:
        if st.session_state.cash > 50:
            allocation = st.session_state.cash * 0.35  # تخصيص 35% من الكاش
            st.session_state.cash -= allocation
            qty = allocation / price
            sl = price * 0.992
            tp = price * 1.008
            
            st.session_state.open_positions[sym] = {
                'entry': price,
                'qty': qty,
                'sl': sl,
                'tp': tp
            }

st.subheader("📌 الصفقات المفتوحة بالأسعار الحية")
if st.session_state.open_positions:
    open_list = []
    for sym, pos in st.session_state.open_positions.items():
        curr_p = current_live_prices.get(sym, pos['entry'])
        pnl_val = (curr_p - pos['entry']) * pos['qty']
        open_list.append({
            'العملة': sym,
            'سعر الدخول الحقيقي': f"${pos['entry']:,.2f}",
            'السعر الحقيقي الآن': f"${curr_p:,.2f}",
            'الربح/الخسارة الحالي': f"${pnl_val:,.2f}"
        })
    st.dataframe(pd.DataFrame(open_list), use_container_width=True)
else:
    st.info("🔄 جاري الاتصال بسيرفرات Binance وجلب الأسعار الحقيقية...")

if st.session_state.trade_history:
    st.subheader("📜 سجل الصفقات المغلقة (أرباح تراكمية حقيقية)")
    st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True)

if auto_refresh:
    time.sleep(8)
    st.rerun()
