

import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import io

# --- CONFIGURACIÓN ESTÉTICA ---
st.set_page_config(page_title="Latin Servicios", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #F5F5DC; }
    h1, h2, h3, p, label { color: #000000 !important; }
    div.stButton > button { 
        background-color: #8DB600; color: black; font-weight: bold; border: 2px solid black; width: 100%;
    }
    .stTabs [aria-selected="true"] { background-color: #8DB600 !important; color: white !important; }
    </style>
    """, unsafe_allow_html=True)

# --- BASE DE DATOS ---
conn = sqlite3.connect('latin_servicios_completo.db', check_same_thread=False)
c = conn.cursor()

# Tablas existentes y nueva tabla de Stock
c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
              precio REAL, estado_pago TEXT, destino TEXT, tipo_mov TEXT, cantidad INTEGER, modelo_bano TEXT)''')
c.execute('CREATE TABLE IF NOT EXISTS stock_playa (id INTEGER PRIMARY KEY, modelo TEXT, color TEXT, cantidad INTEGER)')

c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

# --- SESIÓN ---
if 'login' not in st.session_state:
    st.session_state['login'] = False
    st.session_state['user'] = ""
    st.session_state['rol'] = ""

if not st.session_state['login']:
    st.markdown("<h1 style='text-align: center; color: #8DB600;'>🚚 LATIN SERVICIOS</h1>", unsafe_allow_html=True)
    u = st.text_input("Usuario")
    p = st.text_input("Contraseña", type="password")
    if st.button("INGRESAR"):
        c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
        res = c.fetchone()
        if res:
            st.session_state['login'] = True
            st.session_state['user'] = u
            st.session_state['rol'] = res[0]
            st.rerun()
        else: st.error("Error")
else:
    # --- CÁLCULOS DE STOCK ---
    # Stock en Calle
    df_viajes = pd.read_sql_query("SELECT tipo_mov, cantidad FROM viajes", conn)
    calle = (df_viajes[df_viajes['tipo_mov'] == 'Entregado']['cantidad'].sum() - 
             df_viajes[df_viajes['tipo_mov'] == 'Retirado']['cantidad'].sum()) if not df_viajes.empty else 0

    st.sidebar.title("LATIN SERVICIOS")
    st.sidebar.metric("En la Calle", calle)
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state['login'] = False
        st.rerun()

    # --- PESTAÑAS ---
    pestañas = ["📋 CARGAS", "📊 HISTORIAL", "📦 STOCK EN PLAYA", "🚛 VEHÍCULOS", "👥 USUARIOS"]
    tabs = st.tabs(pestañas) if st.session_state['rol'] == "Administrador" else st.tabs(pestañas[:2])

    # --- PESTAÑA 1: CARGAS (CON ACTUALIZACIÓN DE STOCK) ---
    with tabs[0]:
        st.header("Registrar Movimiento")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        m_list = pd.read_sql_query("SELECT modelo || ' (' || color || ')' as item FROM stock_playa", conn)['item'].tolist()
        
        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            with c1:
                f_cli = st.text_input("Cliente")
                f_tipo = st.radio("Movimiento", ["Entregado", "Retirado"], horizontal=True)
                f_mod = st.selectbox("Modelo de Baño", m_list if m_list else ["Cargar stock en pestaña Playa"])
            with c2:
                f_cant = st.number_input("Cantidad", min_value=1, value=1)
                f_pat = st.selectbox("Vehículo", v_list if v_list else ["Cargar Vehículo"])
                f_dest = st.text_input("Destino")
            
            if st.form_submit_button("GUARDAR Y ACTUALIZAR STOCK"):
                if v_list and m_list:
                    # Guardar Viaje
                    c.execute('''INSERT INTO viajes (fecha, cliente, patente, precio, estado_pago, destino, tipo_mov, cantidad, modelo_bano) 
                                 VALUES (?,?,?,?,?,?,?,?,?)''',
                              (datetime.now().strftime("%d/%m/%Y"), f_cli, f_pat, 0, 'Pendiente', f_dest, f_tipo, f_cant, f_mod))
                    
                    # Actualizar Playa (Si entrego, resto. Si retiro, sumo)
                    mod_nombre = f_mod.split(' (')[0]
                    color_nombre = f_mod.split('(')[1].replace(')', '')
                    operacion = "-" if f_tipo == "Entregado" else "+"
                    c.execute(f"UPDATE stock_playa SET cantidad = cantidad {operacion} ? WHERE modelo = ? AND color = ?", 
                              (f_cant, mod_nombre, color_nombre))
                    
                    conn.commit()
                    st.success(f"Movimiento registrado. Stock en playa actualizado.")
                    st.rerun()

    # --- PESTAÑA 3: STOCK EN PLAYA (GESTIÓN) ---
    with tabs[2]:
        st.header("Inventario en Depósito (Playa)")
        col_s1, col_s2 = st.columns([1, 2])
        
        with col_s1:
            st.subheader("Cargar Nuevo Modelo")
            n_mod = st.text_input("Modelo (ej: Lujo, Obra)")
            n_col = st.text_input("Color (ej: Azul, Verde)")
            n_can = st.number_input("Cantidad Inicial", min_value=0)
            if st.button("AGREGAR A PLAYA"):
                c.execute("INSERT INTO stock_playa (modelo, color, cantidad) VALUES (?,?,?)", (n_mod, n_col, n_can))
                conn.commit()
                st.rerun()
        
        with col_s2:
            st.subheader("Estado Actual en Playa")
            df_playa = pd.read_sql_query("SELECT modelo, color, cantidad FROM stock_playa", conn)
            st.table(df_playa)
            if st.button("Resetear Stock (Admin)"):
                c.execute("DELETE FROM stock_playa")
                conn.commit()
                st.rerun()

    # --- RESTO DE PESTAÑAS (HISTORIAL, VEHÍCULOS, USUARIOS) ---
    with tabs[1]:
        st.header("Historial")
        st.dataframe(pd.read_sql_query("SELECT * FROM viajes", conn), use_container_width=True)
