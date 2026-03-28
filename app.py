import streamlit as st
import pandas as pd
import numpy as np
import time
from datetime import datetime
import plotly.graph_objects as go
import ccxt
import requests
import sqlite3
import json

# ==========================================
# 1. CONFIG & SYSTEM SETTINGS
# ==========================================
st.set_page_config(page_title="🚀 Pro AI Trader V4.1", layout="wide", page_icon="🤖")
DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]
DB_NAME = "ai_trader.db"

# ==========================================
# 2. DATABASE ENGINE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS wallet (id INTEGER PRIMARY KEY, balance REAL, start_balance REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, data TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, coin TEXT, type TEXT, pnl REAL, reason TEXT, snowball TEXT)''')
    
    c.execute("SELECT * FROM wallet WHERE id=1")
    if not c.fetchone():
        c.execute("INSERT INTO wallet (id, balance, start_balance) VALUES (1, 1000.0, 1000.0)")
    conn.commit()
    conn.close()

def sync_positions_to_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM positions")
    for pos in st.session_state.open_positions:
        c.execute("INSERT INTO positions (data) VALUES (?)", (json.dumps(pos),))
    conn.commit()
    conn.close()

def sync_wallet_to_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE wallet SET balance=?, start_balance=? WHERE id=1", (st.session_state.balance, st.session_state.start_balance))
    conn.commit()
    conn.close()

# ==========================================
# 3. STATE MANAGEMENT
# ==========================================
def init_system_state():
    init_db()
    if "is_running" not in st.session_state:
        st.session_state.is_running = False
        st.session_state.is_panic = False
        st.session_state.ai_plan = pd.DataFrame()
        
        # Gerçek veriyi beklemek için başlangıçta her şeyi 0.0 yapıyoruz!
        st.session_state.market_prices = {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0, "ADAUSDT": 0.0}
        st.session_state.sentiment_data = {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0, "ADAUSDT": 0.0}
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT balance, start_balance FROM wallet WHERE id=1")
        wallet_data = c.fetchone()
        st.session_state.balance = wallet_data[0]
        st.session_state.start_balance = wallet_data[1]
        
        st.session_state.open_positions = []
        c.execute("SELECT data FROM positions")
        for row in c.fetchall():
            st.session_state.open_positions.append(json.loads(row[0]))
            
        c.execute("SELECT date, coin, type, pnl, reason, snowball FROM trades ORDER BY id DESC")
        st.session_state.trade_history = pd.DataFrame(c.fetchall(), columns=["Tarih", "Coin", "İşlem", "PnL", "Sebep", "Snowball"])
        conn.close()

# ==========================================
# 4. MARKET ENGINE (Gerçek Veri)
# ==========================================
def fetch_live_market_data(coins: list):
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        for coin in coins:
            ticker = exchange.fetch_ticker(coin.replace("USDT", "/USDT"))
            st.session_state.market_prices[coin] = ticker['last']
    except: pass

    try:
        response = requests.get("https://api.alternative.me/fng/", timeout=5)
        if response.status_code == 200:
            fng_value = int(response.json()['data'][0]['value'])
            normalized_sentiment = (fng_value - 50) / 50.0 
            for coin in coins:
                st.session_state.sentiment_data[coin] = normalized_sentiment
    except: pass

# ==========================================
# 5. STRATEGY ENGINE (MTF + Kelly + Sentiment)
# ==========================================
def calculate_trend_and_volatility(prices: np.ndarray):
    vol = np.std(prices) / np.mean(prices)
    ma_short, ma_long = np.mean(prices[-5:]), np.mean(prices[-20:])
    trend_strength = (ma_short - ma_long) / ma_long
    trend_dir = 1 if trend_strength > 0 else -1
    return trend_dir, abs(trend_strength), vol

def generate_kelly_plan(selected_coins: list, risk_multiplier: float) -> pd.DataFrame:
    plan_data = []
    for coin in selected_coins:
        base_price = st.session_state.market_prices.get(coin, 0.0)
        
        if base_price == 0.0:
            continue # Veri yoksa hesaplama yapma

        # --- MTF (Multi-Timeframe) Simülasyonu ---
        macro_history = np.linspace(base_price*0.8, base_price*1.2, 50) + np.random.randn(50)*(base_price*0.02)
        macro_dir, _, macro_vol = calculate_trend_and_volatility(macro_history)
        
        micro_history = np.linspace(base_price*0.95, base_price*1.05, 50) + np.random.randn(50)*(base_price*0.005)
        micro_dir, micro_str, micro_vol = calculate_trend_and_volatility(micro_history)
        
        mtf_status = "Bilinmiyor"
        p_technical = 0.50
        
        if macro_dir == 1 and micro_dir == 1:
            mtf_status = "Güçlü Boğa 🟢"
            p_technical = np.random.uniform(0.60, 0.75)
        elif macro_dir == -1 and micro_dir == -1:
            mtf_status = "Güçlü Ayı 🔴"
            p_technical = np.random.uniform(0.20, 0.35) 
        elif macro_dir == -1 and micro_dir == 1:
            mtf_status = "Boğa Tuzağı (Fakeout) ⚠️"
            p_technical = np.random.uniform(0.35, 0.45) 
        elif macro_dir == 1 and micro_dir == -1:
            mtf_status = "Düzeltme (Pullback) ⏳"
            p_technical = np.random.uniform(0.50, 0.60) 

        # Dinamik TP/SL
        tp_pct = max(0.02, micro_vol * 3 + (micro_str if micro_dir > 0 else 0))
        sl_pct = max(0.01, micro_vol * 1.5)
        b = tp_pct / sl_pct 
        
        sentiment = st.session_state.sentiment_data.get(coin, 0.0)
        p = max(0.1, min(0.95, p_technical + (sentiment * 0.15))) 
        
        # Kelly Kriteri
        kelly_fraction = p - ((1.0 - p) / b)
        
        if kelly_fraction <= 0:
            alloc_pct = 0.0
            status = "Pas (Riskli)"
        else:
            alloc_pct = min(kelly_fraction * (risk_multiplier / 10.0), 0.25)
            status = f"Aktif (K=%{alloc_pct*100:.1f})"

        plan_data.append({
            "Coin": coin,
            "MTF Durumu": mtf_status,
            "R/R": f"{b:.2f}",
            "Win Rate": f"%{p*100:.1f}",
            "Margin Pct": alloc_pct,
            "Marjin Önerisi": f"%{alloc_pct*100:.1f}",
            "Durum": status,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct
        })
    return pd.DataFrame(plan_data).sort_values(by="Margin Pct", ascending=False) if plan_data else pd.DataFrame()

# ==========================================
# 6. EXECUTION ENGINE
# ==========================================
def log_trade(coin: str, pnl: float, reason: str, snowball: str = "-", is_backtest=False):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_trade = pd.DataFrame([{"Tarih": date_str, "Coin": coin, "İşlem": "KAPAT", "PnL": round(pnl, 2), "Sebep": reason, "Snowball": snowball}])
    
    if not is_backtest:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO trades (date, coin, type, pnl, reason, snowball) VALUES (?, ?, ?, ?, ?, ?)",
                  (date_str, coin, "KAPAT", round(pnl, 2), reason, snowball))
        conn.commit()
        conn.close()
        st.session_state.trade_history = pd.concat([new_trade, st.session_state.trade_history], ignore_index=True)
    return new_trade

def manage_open_positions(is_backtest=False):
    retained_positions = []
    state_changed = False
    
    for pos in st.session_state.open_positions:
        curr_price = st.session_state.market_prices.get(pos["coin"], 0.0)
        if curr_price == 0.0:
            retained_positions.append(pos)
            continue
        
        # Trailing Stop Update
        if curr_price > pos["highest_price"]:
            pos["highest_price"] = curr_price
            pos["sl"] = curr_price * (1 - pos["sl_pct"]/2) 
            state_changed = True
        
        # Exit Conditions
        if curr_price <= pos["sl"] or curr_price >= pos["tp"]:
            pnl = (curr_price - pos["entry"]) * pos["size"]
            st.session_state.balance += pos["margin"] + pnl
            log_trade(pos["coin"], pnl, "Trailing Stop" if curr_price <= pos["sl"] else "Take Profit", pos.get("snowball", "-"), is_backtest)
            state_changed = True
        else:
            pos["current_pnl"] = (curr_price - pos["entry"]) * pos["size"]
            retained_positions.append(pos)
            
    st.session_state.open_positions = retained_positions
    if state_changed and not is_backtest:
        sync_positions_to_db()
        sync_wallet_to_db()

def execute_new_trades(plan_df: pd.DataFrame, daily_profit: float, settings: dict, is_backtest=False):
    if plan_df.empty or len(st.session_state.open_positions) >= len(settings["coins"]) or st.session_state.balance < 50:
        return 

    top_trade = plan_df.iloc[0]
    coin = top_trade["Coin"]
    alloc_pct = top_trade["Margin Pct"]
    
    if alloc_pct <= 0 or any(p["coin"] == coin for p in st.session_state.open_positions):
        return

    entry_price = st.session_state.market_prices[coin]
    final_margin = st.session_state.balance * alloc_pct
    snowball_applied = False

    if settings["snowball"] and daily_profit > settings["daily_target"]:
        final_margin += (daily_profit - settings["daily_target"]) * 0.50
        snowball_applied = True

    if final_margin < st.session_state.balance:
        st.session_state.balance -= final_margin
        st.session_state.open_positions.append({
            "coin": coin, "entry": entry_price, "size": (final_margin * 10) / entry_price, 
            "margin": final_margin, "highest_price": entry_price,
            "sl": entry_price * (1 - top_trade["sl_pct"]), "tp": entry_price * (1 + top_trade["tp_pct"]),
            "sl_pct": top_trade["sl_pct"], "current_pnl": 0.0,
            "snowball": "Aktif 🔥" if snowball_applied else "-"
        })
        if not is_backtest:
            sync_positions_to_db()
            sync_wallet_to_db()

# ==========================================
# 7. BACKTEST ENGINE
# ==========================================
def run_backtest(settings, days):
    bt_balance = 1000.0
    bt_start_balance = 1000.0
    st.session_state.open_positions = [] 
    st.session_state.balance = bt_balance
    
    iterations = days * 24 
    max_drawdown = 0.0
    peak_balance = bt_balance
    progress_bar = st.progress(0)
    
    for i in range(iterations):
        for coin in settings["coins"]:
            base = st.session_state.market_prices.get(coin, 100)
            st.session_state.market_prices[coin] = base * (1 + np.random.normal(0.0002, 0.005))
            st.session_state.sentiment_data[coin] = np.random.normal(0, 0.3)
            
        manage_open_positions(is_backtest=True)
        plan = generate_kelly_plan(settings["coins"], settings["risk_mult"])
        daily_profit = st.session_state.balance - bt_start_balance
        execute_new_trades(plan, daily_profit, settings, is_backtest=True)
        
        if st.session_state.balance > peak_balance:
            peak_balance = st.session_state.balance
        current_dd = (peak_balance - st.session_state.balance) / peak_balance
        if current_dd > max_drawdown: max_drawdown = current_dd
        if i % 10 == 0: progress_bar.progress(i / iterations)

    progress_bar.progress(1.0)
    
    for pos in st.session_state.open_positions:
        pnl = (st.session_state.market_prices[pos["coin"]] - pos["entry"]) * pos["size"]
        st.session_state.balance += pos["margin"] + pnl
    
    st.session_state.open_positions = [] 
    net_profit = st.session_state.balance - bt_start_balance
    st.success("✅ Backtest Tamamlandı!")
    c1, c2, c3 = st.columns(3)
    c1.metric("Test Sonu Bakiye", f"${st.session_state.balance:.2f}", f"${net_profit:.2f}")
    c2.metric("Net Getiri (%)", f"%{(net_profit/bt_start_balance)*100:.1f}")
    c3.metric("Maksimum Düşüş (Drawdown)", f"%{max_drawdown*100:.1f}", delta_color="inverse")
    
    init_system_state()

# ==========================================
# 8. UI & ORCHESTRATOR
# ==========================================
def main():
    st.title("🚀 Pro AI Trader V4.1 (Live Ready)")
    init_system_state()
    
    with st.sidebar:
        st.header("⚙️ Konfigürasyon")
        app_mode = st.radio("Sistem Modu:", ["🔴 Canlı İşlem", "⏪ Backtest Modu"])
        settings = {
            "coins": st.multiselect("Varlıklar:", DEFAULT_COINS, default=DEFAULT_COINS),
            "daily_target": st.number_input("Günlük Hedef ($):", 50, value=150, step=50),
            "risk_mult": st.slider("Kelly Çarpanı (Risk):", 1, 5, 2),
            "snowball": st.toggle("🔥 Snowball Modu", value=True)
        }
        
        st.markdown("---")
        if app_mode == "🔴 Canlı İşlem":
            c1, c2 = st.columns(2)
            if c1.button("🟢 Başlat/Durdur", use_container_width=True):
                st.session_state.is_running = not st.session_state.is_running
                st.session_state.is_panic = False
            if c2.button("🛑 PANİK!", use_container_width=True, type="primary"):
                st.session_state.is_panic = True
        else:
            bt_days = st.slider("Backtest Süresi (Gün)", 7, 90, 30)
            if st.button("⏪ Backtesti Başlat", use_container_width=True):
                run_backtest(settings, bt_days)

    if app_mode == "🔴 Canlı İşlem":
        m1, m2, m3, m4 = st.columns(4)
        met_bal = m1.empty()
        met_prof = m2.empty()
        met_pos = m3.empty()
        met_stat = m4.empty()

        st.markdown("---")
        col_left, col_right = st.columns([1.5, 1])
        with col_left:
            st.subheader("💹 Açık Pozisyonlar")
            tbl_open = st.empty()
            st.subheader("🤖 AI Karar Matrisi (MTF + Kelly)")
            tbl_ai = st.empty()
        with col_right:
            st.subheader("📈 Fiyat Aksiyonu")
            chart_plt = st.empty()
            st.subheader("📝 İşlem Defteri")
            tbl_hist = st.empty()

        daily_profit = st.session_state.balance - st.session_state.start_balance

        if st.session_state.is_panic:
            if len(st.session_state.open_positions) > 0:
                for pos in st.session_state.open_positions:
                    pnl = (st.session_state.market_prices[pos["coin"]] - pos["entry"]) * pos["size"]
                    st.session_state.balance += pos["margin"] + pnl
                    log_trade(pos["coin"], pnl, "PANİK BUTONU")
                st.session_state.open_positions = []
                sync_positions_to_db()
                sync_wallet_to_db()
            st.session_state.is_running = False
            met_stat.metric("Durum", "🔴 PANİK MODU")
            st.error("🚨 Tüm pozisyonlar kapatıldı.")
            
        elif st.session_state.is_running:
            met_stat.metric("Durum", "🟢 AKTİF")
            while st.session_state.is_running:
                fetch_live_market_data(settings["coins"])
                
                # --- YENİ EKLENEN CANLI VERİ KONTROLÜ ---
                if any(st.session_state.market_prices.get(coin, 0.0) == 0.0 for coin in settings["coins"]):
                    st.warning("⏳ Binance'ten canlı veri çekiliyor... Lütfen bekleyin.")
                    time.sleep(2)
                    st.rerun()
                # ----------------------------------------
                
                manage_open_positions()
                st.session_state.ai_plan = generate_kelly_plan(settings["coins"], settings["risk_mult"])
                daily_profit = st.session_state.balance - st.session_state.start_balance
                execute_new_trades(st.session_state.ai_plan, daily_profit, settings)

                # UI Updates
                met_bal.metric("Kasa Bakiyesi", f"${st.session_state.balance:,.2f}")
                met_prof.metric("Günlük Kâr", f"${daily_profit:,.2f}")
                met_pos.metric("Açık İşlem", len(st.session_state.open_positions))
                
                if st.session_state.open_positions:
                    df_open = pd.DataFrame(st.session_state.open_positions)[["coin", "entry", "sl", "tp", "margin", "current_pnl", "snowball"]]
                    df_open["margin"] = df_open["margin"].map("${:,.2f}".format)
                    tbl_open.dataframe(df_open, hide_index=True, use_container_width=True)
                else:
                    tbl_open.info("MTF onayı ve Edge bekleniyor...")
                    
                if not st.session_state.ai_plan.empty:
                    display_plan = st.session_state.ai_plan.drop(columns=["Margin Pct", "tp_pct", "sl_pct"])
                    tbl_ai.dataframe(display_plan, hide_index=True, use_container_width=True)
                
                if not st.session_state.trade_history.empty:
                    styled_hist = st.session_state.trade_history.head(5).style.applymap(lambda v: 'color: green' if v > 0 else 'color: red', subset=['PnL'])
                    tbl_hist.dataframe(styled_hist, hide_index=True, use_container_width=True)

                fig = go.Figure()
                for coin in settings["coins"]:
                    base_price = st.session_state.market_prices.get(coin, 0.0)
                    if base_price > 0:
                        hist = [base_price * (1 + np.random.normal(0, 0.005)) for _ in range(15)]
                        fig.add_trace(go.Scatter(y=hist, mode='lines', name=coin))
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=300, template="plotly_dark")
                chart_plt.plotly_chart(fig, use_container_width=True)

                time.sleep(2)
                st.rerun()
        else:
            met_stat.metric("Durum", "⚪ BEKLEMEDE")
            met_bal.metric("Kasa Bakiyesi", f"${st.session_state.balance:,.2f}")
            met_prof.metric("Günlük Kâr", f"${daily_profit:,.2f}")
            met_pos.metric("Açık İşlem", len(st.session_state.open_positions))

if __name__ == "__main__":
    main()