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

# --- BASE DE DATOS (Nombre definitivo para no perder datos) ---
conn = sqlite3.connect('latin_servicios_oficial.db', check_same_thread=False)
c = conn.cursor()

c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
              destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER, 
              tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL, estado_pago TEXT)''')
c.execute('CREATE TABLE IF NOT EXISTS stock_playa (nro_unit TEXT PRIMARY KEY, modelo TEXT, estado TEXT)')

# Aseguramos el Admin
c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

# --- LÓGICA DE LOGIN ---
if 'login' not in st.session_state:
    st.session_state.login = False
    st.session_state.rol = ""
    st.session_state.user_active = ""

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center; color: #8DB600;'>🚚 LATIN SERVICIOS</h1>", unsafe_allow_html=True)
    col_log1, col_log2, col_log3 = st.columns([1,1,1])
    with col_log2:
        u_input = st.text_input("Usuario").strip() # .strip() quita espacios accidentales
        p_input = st.text_input("Contraseña", type="password").strip()
        if st.button("INGRESAR AL SISTEMA"):
            c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u_input, p_input))
            res = c.fetchone()
            if res:
                st.session_state.login = True
                st.session_state.rol = res[0]
                st.session_state.user_active = u_input
                st.rerun()
            else:
                st.error("❌ Usuario o clave incorrectos. Verifique en la pestaña de Usuarios como Admin.")
else:
    # --- PANEL DE CONTROL ---
    st.sidebar.title("LATIN SERVICIOS")
    st.sidebar.write(f"Conectado: **{st.session_state.user_active}**")
    st.sidebar.write(f"Permisos: **{st.session_state.rol}**")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.login = False
        st.rerun()

    # --- PESTAÑAS ---
    if st.session_state.rol == "Administrador":
        tabs = st.tabs(["📋 CARGAS", "📊 HISTORIAL", "📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"])
    else:
        tabs = st.tabs(["📋 CARGAS", "📊 HISTORIAL"])

    # 1. CARGAS
    with tabs[0]:
        st.header("Registro de Movimiento")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_f = "En Playa" if mov == "Entregado" else "En Calle"
        u_dispo = pd.read_sql_query(f"SELECT nro_unit FROM stock_playa WHERE estado='{est_f}'", conn)['nro_unit'].tolist()
        
        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            f_cli = c1.text_input("Cliente / Obra")
            f_dest = c1.text_input("Dirección")
            f_tipo_c = c1.selectbox("Contratación", ["Mensual (Obra)", "Eventual (Evento)"])
            f_km = c1.number_input("Km recorridos", min_value=0.0)
            f_units = c2.multiselect("Unidades", u_dispo)
            f_pat = c2.selectbox("Vehículo", v_list if v_list else ["Sin Patentes"])
            f_prec = c2.number_input("Precio Unitario ($)", min_value=0.0)
            f_pago = c2.selectbox("Estado Pago", ["Pendiente", "Pagado"])
            
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
                    st.success("✅ Registro guardado y stock actualizado.")
                    st.rerun()

    # 2. HISTORIAL
    with tabs[1]:
        st.header("Historial")
        df_h = pd.read_sql_query("SELECT * FROM viajes", conn)
        st.dataframe(df_h, use_container_width=True)
        if not df_h.empty:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_h.to_excel(writer, index=False)
            st.download_button("📥 DESCARGAR EXCEL", output.getvalue(), "Latin_Reporte.xlsx")

    # SOLO ADMIN
    if st.session_state.rol == "Administrador":
        with tabs[2]: # STOCK
            st.subheader("Cargar Baños")
            nu = st.text_input("Nº Unidad")
            mo = st.text_input("Modelo")
            if st.button("Guardar"):
                c.execute("INSERT OR IGNORE INTO stock_playa VALUES (?,?,'En Playa')", (nu, mo))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM stock_playa", conn))

        with tabs[3]: # VEHICULOS
            st.subheader("Cargar Vehículos")
            pat = st.text_input("Patente").upper()
            if st.button("Cargar Patente"):
                c.execute("INSERT OR IGNORE INTO vehiculos VALUES (?,?)", (pat, "Camión"))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

        with tabs[4]: # USUARIOS
            st.subheader("Gestión de Usuarios")
            un = st.text_input("Nombre Operador")
            pn = st.text_input("Clave Operador", type="password")
            if st.button("Crear Usuario"):
                c.execute("INSERT OR IGNORE INTO usuarios VALUES (?,?,'Operador')", (un, pn))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT user, rol FROM usuarios", conn))
