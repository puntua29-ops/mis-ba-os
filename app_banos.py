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
        background-color: #8DB600; color: black; font-weight: bold; border: 2px solid black; 
    }
    </style>
    """, unsafe_allow_html=True)

# --- BASE DE DATOS ---
conn = sqlite3.connect('latin_servicios_v6.db', check_same_thread=False)
c = conn.cursor()

c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
              destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER, 
              tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL, estado_pago TEXT)''')
c.execute('CREATE TABLE IF NOT EXISTS stock_playa (nro_unit TEXT PRIMARY KEY, modelo TEXT, estado TEXT)')

c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

# --- LOGIN ---
if 'login' not in st.session_state:
    st.session_state.login = False
    st.session_state.rol = ""

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center; color: #8DB600;'>🚚 LATIN SERVICIOS</h1>", unsafe_allow_html=True)
    u = st.text_input("Usuario")
    p = st.text_input("Contraseña", type="password")
    if st.button("INGRESAR"):
        c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
        res = c.fetchone()
        if res:
            st.session_state.login, st.session_state.rol = True, res[0]
            st.rerun()
        else: st.error("Acceso denegado")
else:
    # --- SIDEBAR ---
    st.sidebar.title("LATIN SERVICIOS")
    st.sidebar.write(f"Rol: **{st.session_state.rol}**")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.login = False
        st.rerun()

    # --- PESTAÑAS SEGÚN ROL ---
    p_lista = ["📋 CARGAS", "📊 HISTORIAL", "📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]
    tabs = st.tabs(p_lista if st.session_state.rol == "Administrador" else p_lista[:2])

    # 1. CARGAS
    with tabs[0]:
        st.header("Registro de Movimiento")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_f = "En Playa" if mov == "Entregado" else "En Calle"
        u_dispo = pd.read_sql_query(f"SELECT nro_unit FROM stock_playa WHERE estado='{est_f}'", conn)['nro_unit'].tolist()
        
        with st.form("form_viajes"):
            col1, col2 = st.columns(2)
            with col1:
                f_cli = st.text_input("Cliente / Obra")
                f_dest = st.text_input("Dirección")
                f_tipo_c = st.selectbox("Contratación", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km = st.number_input("Km recorridos", min_value=0.0)
            with col2:
                f_units = st.multiselect("Unidades (Puede elegir varias)", u_dispo)
                f_pat = st.selectbox("Vehículo", v_list if v_list else ["Cargar Patente"])
                f_prec = st.number_input("Precio Unitario ($)", min_value=0.0)
                f_pago = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])
            
            if st.form_submit_button("GUARDAR MOVIMIENTO"):
                if f_units and v_list:
                    total = len(f_units) * f_prec
                    str_u = ", ".join(f_units)
                    nuevo_est = "En Calle" if mov == "Entregado" else "En Playa"
                    c.execute("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                              unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago) 
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (datetime.now().strftime("%d/%m/%Y"), f_cli, f_pat, f_dest, mov, 
                               str_u, len(f_units), f_tipo_c, f_km, f_prec, total, f_pago))
                    for unit in f_units:
                        c.execute("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_est, unit))
                    conn.commit()
                    st.success("✅ Guardado")
                    st.rerun()

    # 2. HISTORIAL
    with tabs[1]:
        st.header("Historial de Movimientos")
        df_h = pd.read_sql_query("SELECT * FROM viajes", conn)
        st.dataframe(df_h, use_container_width=True)
        if not df_h.empty:
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df_h.to_excel(writer, index=False)
                st.download_button(label="📥 DESCARGAR EXCEL", data=output.getvalue(), file_name="Latin_Reporte.xlsx")
            except: st.error("Error al generar Excel")

    # SOLO ADMIN
    if st.session_state.rol == "Administrador":
        with tabs[2]: # STOCK
            st.header("Gestión de Unidades")
            col_a, col_b = st.columns([1,2])
            with col_a:
                nu = st.text_input("Nº Unidad")
                mo = st.text_input("Modelo")
                if st.button("Cargar Baño"):
                    c.execute("INSERT OR IGNORE INTO stock_playa VALUES (?,?,'En Playa')", (nu, mo))
                    conn.commit(); st.rerun()
            with col_b: st.table(pd.read_sql_query("SELECT * FROM stock_playa", conn))

        with tabs[3]: # VEHICULOS
            st.header("Flota")
            pat = st.text_input("Patente").upper()
            if st.button("Cargar"):
                c.execute("INSERT OR IGNORE INTO vehiculos VALUES (?,?)", (pat, "Unidad"))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

        with tabs[4]: # USUARIOS
            st.header("Usuarios")
            un = st.text_input("Nombre Usuario")
            pn = st.text_input("Clave", type="password")
            if st.button("Crear Operador"):
                c.execute("INSERT OR IGNORE INTO usuarios VALUES (?,?,'Operador')", (un, pn))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT user, rol FROM usuarios", conn))
