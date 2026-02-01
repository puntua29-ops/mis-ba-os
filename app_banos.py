import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import io
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim

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

# --- BASE DE DATOS (VERSION v15) ---
conn = sqlite3.connect('latin_v15.db', check_same_thread=False)
c = conn.cursor()

c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
              destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER, 
              tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL, 
              estado_pago TEXT, lat REAL, lon REAL)''')
c.execute('CREATE TABLE IF NOT EXISTS stock_playa (nro_unit TEXT PRIMARY KEY, tipo TEXT, modelo TEXT, estado TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS personal (id INTEGER PRIMARY KEY, fecha TEXT, nombre TEXT, tarea TEXT, pago REAL)')
c.execute('CREATE TABLE IF NOT EXISTS gastos (id INTEGER PRIMARY KEY, fecha TEXT, patente TEXT, concepto TEXT, monto REAL)')

c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

# --- CONFIGURACIÓN DE GPS ---
geolocator = Nominatim(user_agent="latin_servicios_v15")

# --- LOGIN ---
if 'login' not in st.session_state:
    st.session_state.login = False

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center;'>🚚 LATIN SERVICIOS</h1>", unsafe_allow_html=True)
    u = st.text_input("Usuario")
    p = st.text_input("Contraseña", type="password")
    if st.button("INGRESAR"):
        c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
        res = c.fetchone()
        if res:
            st.session_state.login, st.session_state.rol = True, res[0]
            st.rerun()
        else: 
            st.error("Acceso incorrecto")
else:
    st.sidebar.title("LATIN SERVICIOS")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.login = False
        st.rerun()

    # Definición de Pestañas
    titulos = ["📋 CARGAS", "🗺️ MAPA", "📊 HISTORIAL", "👷 PERSONAL", "⛽ GASTOS", "💰 BALANCE"]
    if st.session_state.rol == "Administrador":
        titulos += ["📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]
    
    tabs = st.tabs(titulos)

    # --- PESTAÑA 0: CARGAS ---
    with tabs[0]:
        st.header("Registro de Movimiento")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_f = "En Playa" if mov == "Entregado" else "En Calle"
        u_dispo = pd.read_sql_query(f"SELECT nro_unit FROM stock_playa WHERE estado='{est_f}'", conn)['nro_unit'].tolist()
        
        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            with c1:
                f_cli = st.text_input("Cliente / Obra")
                f_dest = st.text_input("Dirección de Entrega")
                f_tipo_c = st.selectbox("Contrato", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km = st.number_input("Km", min_value=0.0)
            with c2:
                f_units = st.multiselect("Nº Unidades", u_dispo)
                f_pat = st.selectbox("Vehículo", v_list if v_list else ["Sin Patente"])
                f_prec = st.number_input("Precio Unitario ($)", min_value=0.0)
                f_pago = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])
            
            if st.form_submit_button("GUARDAR"):
                if f_units:
                    lat, lon = None, None
                    try:
                        location = geolocator.geocode(f"{f_dest}, Cordoba, Argentina", timeout=10)
                        if location: lat, lon = location.latitude, location.longitude
                    except: pass
                    fecha_h = datetime.now().strftime("%d/%m/%Y %H:%M")
                    str_u = ", ".join(f_units)
                    total_v = len(f_units) * f_prec
                    nuevo_e = "En Calle" if mov == "Entregado" else "En Playa"
                    c.execute("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                                 unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago, lat, lon) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (fecha_h, f_cli, f_pat, f_dest, mov, str_u, len(f_units), f_tipo_c, f_km, f_prec, total_v, f_pago, lat, lon))
                    for unit in f_units:
                        c.execute("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_e, unit))
                    conn.commit()
                    st.success("✅ Guardado.")
                    st.rerun()

    # --- PESTAÑA 1: MAPA ---
    with tabs[1]:
        st.header("Ubicación de Unidades")
        query = "SELECT cliente, destino, unidades, lat, lon FROM viajes WHERE tipo_mov = 'Entregado' AND lat IS NOT NULL"
        df_mapa = pd.read_sql_query(query, conn)
        m = folium.Map(location=[-31.4135, -64.1810], zoom_start=12)
        for _, row in df_mapa.iterrows():
            folium.Marker([row['lat'], row['lon']], popup=f"{row['cliente']}: {row['unidades']}").add_to(m)
        st_folium(m, width=1100, height=500)

    # --- PESTAÑA 2: HISTORIAL + EDITOR ---
    with tabs[2]:
        st.header("Historial")
        df_h = pd.read_sql_query("SELECT * FROM viajes", conn)
        st.dataframe(df_h, use_container_width=True)
        st.write("---")
        if not df_h.empty:
            id_editar = st.selectbox("ID a corregir", [""] + df_h['id'].tolist())
            if id_editar != "":
                viaje_sel = df_h[df_h['id'] == id_editar].iloc[0]
                with st.form("form_edit"):
                    e_cli = st.text_input("Cliente", value=viaje_sel['cliente'])
                    e_dest = st.text_input("Dirección", value=viaje_sel['destino'])
                    e_prec = st.number_input("Precio ($)", value=float(viaje_sel['precio_unit']))
                    if st.form_submit_button("APLICAR CAMBIOS"):
                        nuevo_total = viaje_sel['cantidad'] * e_prec
                        c.execute("UPDATE viajes SET cliente=?, destino=?, precio_unit=?, total=? WHERE id=?", (e_cli, e_dest, e_prec, nuevo_total, id_editar))
                        conn.commit(); st.rerun()

    # --- PESTAÑA 3: PERSONAL ---
    with tabs[3]:
        st.header("👷 Personal")
        with st.form("personal_f"):
            c1, c2, c3 = st.columns(3)
            p_nom = c1.text_input("Nombre")
            p_tar = c2.text_input("Tarea")
            p_mon = c3.number_input("Pago ($)", min_value=0.0)
            if st.form_submit_button("REGISTRAR PAGO"):
                c.execute("INSERT INTO personal (fecha, nombre, tarea, pago) VALUES (?,?,?,?)", (datetime.now().strftime("%d/%m/%Y"), p_nom, p_tar, p_mon))
                conn.commit(); st.rerun()
        st.dataframe(pd.read_sql_query("SELECT * FROM personal", conn), use_container_width=True)

    # --- PESTAÑA 4: GASTOS ---
    with tabs[4]:
        st.header("⛽ Gastos")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        with st.form("gastos_f"):
            c1, c2, c3 = st.columns(3)
            g_pat = c1.selectbox("Vehículo", v_list if v_list else ["S/P"])
            g_con = c2.selectbox("Concepto", ["Combustible", "Aceite", "Repuestos", "Limpieza", "Otros"])
            g_mon = c3.number_input("Monto ($)", min_value=0.0)
            if st.form_submit_button("CARGAR GASTO"):
                c.execute("INSERT INTO gastos (fecha, patente, concepto, monto) VALUES (?,?,?,?)", (datetime.now().strftime("%d/%m/%Y"), g_pat, g_con, g_mon))
                conn.commit(); st.rerun()
        st.dataframe(pd.read_sql_query("SELECT * FROM gastos", conn), use_container_width=True)

    # --- PESTAÑA 5: BALANCE ---
    with tabs[5]:
        st.header("💰 Balance de Caja")
        ingresos = pd.read_sql_query("SELECT SUM(total) FROM viajes", conn).iloc[0,0] or 0
        egresos_pers = pd.read_sql_query("SELECT SUM(pago) FROM personal", conn).iloc[0,0] or 0
        egresos_gastos = pd.read_sql_query("SELECT SUM(monto) FROM gastos", conn).iloc[0,0] or 0
        neto = ingresos - (egresos_pers + egresos_gastos)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ingresos", f"${ingresos:,.2f}")
        col2.metric("Personal", f"-${egresos_pers:,.2f}")
        col3.metric("Gastos", f"-${egresos_gastos:,.2f}")
        col4.metric("NETO", f"${neto:,.2f}", delta=neto)

    # --- PESTAÑAS ADMIN ---
    if st.session_state.rol == "Administrador":
        with tabs[6]: # STOCK
            st.header("📦 Stock")
            col1, col2, col3 = st.columns(3)
            tipo_u = col1.selectbox("Tipo", ["Baño Químico", "Contenedor", "Oficina Móvil"])
            nu = col2.text_input("ID Unidad")
            mo = col3.text_input("Modelo")
            if st.button("GUARDAR STOCK"):
                c.execute("INSERT OR IGNORE INTO stock_playa VALUES (?,?,?, 'En Playa')", (nu, tipo_u, mo))
                conn.commit(); st.rerun()
            df_stock = pd.read_sql_query("SELECT * FROM stock_playa", conn)
            st.dataframe(df_stock, use_container_width=True)
            st.write("---")
            u_borrar = st.selectbox("Borrar Unidad", [""] + df_stock['nro_unit'].tolist())
            if st.button("❌ ELIMINAR UNIDAD"):
                c.execute("DELETE FROM stock_playa WHERE nro_unit=?", (u_borrar,))
                conn.commit(); st.rerun()

        with tabs[7]: # VEHÍCULOS
            st.header("🚛 Vehículos")
            pa = st.text_input("Patente Camión").upper()
            if st.button("CARGAR CAMIÓN"):
                c.execute("INSERT OR IGNORE INTO vehiculos VALUES (?,?)", (pa, "Unidad"))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

        with tabs[8]: # USUARIOS
            st.header("👥 Usuarios")
            un = st.text_input("Usuario")
            pn = st.text_input("Clave", type="password")
            if st.button("CREAR"):
                c.execute("INSERT OR IGNORE INTO usuarios VALUES (?,?,'Operador')", (un, pn))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT user, rol FROM usuarios", conn))
