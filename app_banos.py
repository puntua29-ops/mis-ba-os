import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import time
import os
import hashlib
import threading

import database

# ─────────────────────────────────────────────
# CONFIGURACIÓN ESTÉTICA
# ─────────────────────────────────────────────
st.set_page_config(page_title="Servicios de Logística", layout="wide")
st.markdown("""
    <style>
    .stApp { background-color: #F5F5DC; }
    h1, h2, h3, p, label { color: #000000 !important; }
    div.stButton > button {
        background-color: #8DB600; color: black; font-weight: bold; border: 2px solid black;
    }
    </style>
""", unsafe_allow_html=True)

IS_CLOUD = 'STREAMLIT_RUNTIME_ENV' in os.environ
DB_PATH  = 'gestion_banos.db'

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# CAPA DE BASE DE DATOS (SUPABASE)
# ─────────────────────────────────────────────

def run_query(query, params=(), commit=False):
    """Ejecuta una query en Supabase de forma segura."""
    # Convertir '?' (SQLite style) a '%s' (psycopg2 style) para compatibilidad si es necesario
    query = query.replace('?', '%s')
    
    # Manejar el nombre reservado "user" en Postgres
    query = query.replace('usuarios WHERE user=', 'usuarios WHERE "user"=')
    query = query.replace('INSERT INTO usuarios VALUES', 'INSERT INTO usuarios ("user", password, rol, sucursal) VALUES')
    query = query.replace('DELETE FROM usuarios WHERE user=', 'DELETE FROM usuarios WHERE "user"=')
    query = query.replace('SELECT user, rol, sucursal', 'SELECT "user", rol, sucursal')
    query = query.replace('FROM usuarios WHERE user=', 'FROM usuarios WHERE "user"=')
    
    try:
        return database.run_query(query, params, fetch=not commit)
    except Exception as e:
        st.error(f"❌ Error en Base de Datos: {e}")
        return []

def _init_db_once():
    """Inicialización legacy (ahora manejada por Supabase)."""
    return True

def init_db_cloud():
    """Inicialización legacy."""
    return True


# ─────────────────────────────────────────────
# INICIALIZACIÓN DE TABLAS
# También usa el recurso cacheado para evitar
# conflictos durante el arranque.
# ─────────────────────────────────────────────

@st.cache_resource
def _init_db_once():
    """Crea las tablas y el usuario admin UNA sola vez por proceso."""
    if IS_CLOUD:
        return  # La inicialización cloud se maneja en init_db_cloud()

    conn, lock = _get_sqlite_resources()
    with lock:
        c = conn.cursor()
        # Crear tablas
        c.execute("""CREATE TABLE IF NOT EXISTS usuarios
                     (user TEXT PRIMARY KEY, password TEXT, rol TEXT, sucursal TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS vehiculos
                     (patente TEXT PRIMARY KEY, modelo TEXT, sucursal TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS viajes
                     (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT,
                      destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER,
                      tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL,
                      estado_pago TEXT, lat REAL, lon REAL, sucursal TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS stock_playa
                     (nro_unit TEXT PRIMARY KEY, tipo TEXT, modelo TEXT, estado TEXT, sucursal TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS personal
                     (id INTEGER PRIMARY KEY, fecha TEXT, nombre TEXT, tarea TEXT, pago REAL, sucursal TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS gastos
                     (id INTEGER PRIMARY KEY, fecha TEXT, patente TEXT, concepto TEXT, monto REAL, sucursal TEXT)""")

        # Migración: agregar columna sucursal si falta en alguna tabla
        for tabla in ["usuarios","vehiculos","viajes","stock_playa","personal","gastos"]:
            try:
                c.execute(f"PRAGMA table_info({tabla})")
                cols_actuales = [row[1] for row in c.fetchall()]
                if "sucursal" not in cols_actuales:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN sucursal TEXT DEFAULT 'Sucursal A'")
            except Exception:
                pass

        conn.commit()

        # Insertar usuario administrador si no existe
        try:
            c.execute(
                "INSERT INTO usuarios VALUES (?, ?, 'Administrador', 'Todas')",
                ('marcelo', hash_password('@Lex2110'))
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # Ya existe, no hay problema

    return True

def init_db_cloud():
    """Inicialización para Google Sheets (cloud)."""
    gconn = get_gsheets_connection()
    if not gconn:
        return
    tablas = {
        "usuarios":   ["user","password","rol","sucursal"],
        "vehiculos":  ["patente","modelo","sucursal"],
        "viajes":     ["id","fecha","cliente","patente","destino","tipo_mov","unidades",
                       "cantidad","tipo_contrato","km_entrega","precio_unit","total",
                       "estado_pago","lat","lon","sucursal"],
        "stock_playa":["nro_unit","tipo","modelo","estado","sucursal"],
        "personal":   ["id","fecha","nombre","tarea","pago","sucursal"],
        "gastos":     ["id","fecha","patente","concepto","monto","sucursal"]
    }
    for nombre, cols in tablas.items():
        try:
            df_actual = gconn.read(worksheet=nombre, ttl=0)
            missing = [c for c in cols if c not in df_actual.columns]
            if missing:
                for col in missing:
                    df_actual[col] = "Todas" if nombre == "usuarios" else "Sucursal A"
                gconn.update(worksheet=nombre, data=df_actual)
        except Exception as e:
            if "Worksheet not found" in str(e) or "404" in str(e):
                try:
                    df_init = pd.DataFrame(columns=cols)
                    if nombre == "usuarios":
                        df_init.loc[0] = ["marcelo", hash_password("@Lex2110"), "Administrador", "Todas"]
                    gconn.update(worksheet=nombre, data=df_init)
                except Exception as e2:
                    st.error(f"❌ No se pudo crear la pestaña '{nombre}': {e2}")
            else:
                st.error(f"❌ Error al verificar pestaña '{nombre}': {e}")

# ── Ejecutar inicialización ──────────────────
try:
    if IS_CLOUD:
        init_db_cloud()
    else:
        _init_db_once()  # cached: corre solo una vez
except Exception as e:
    err_str = str(e)
    if IS_CLOUD:
        if "403" in err_str or "PERMISSION_DENIED" in err_str:
            st.error("🔒 Sin permisos de escritura en Google Sheets.")
        else:
            st.warning(f"⚠️ Problema al conectar Google Sheets: {err_str}")
    else:
        st.error(f"Error al inicializar base de datos: {err_str}")

# ─────────────────────────────────────────────
# GPS
# ─────────────────────────────────────────────
geolocator = Nominatim(user_agent="servicios_logistica_v2")

# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────
if 'login' not in st.session_state:
    st.session_state.login = False

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    _, c2, _ = st.columns([1, 1, 1])
    with c2:
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("INGRESAR", use_container_width=True):
            with st.spinner("Verificando..."):
                res = run_query(
                    "SELECT rol, sucursal FROM usuarios WHERE \"user\"=%s AND password=%s",
                    (u, hash_password(p))
                )
                if res:
                    # Supabase returns list of dicts via RealDictCursor
                    user_data = res[0]
                    st.session_state.login    = True
                    st.session_state.rol      = user_data['rol']
                    st.session_state.sucursal = user_data['sucursal']
                    st.session_state.user     = u
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos")

    with st.expander("🛠️ Diagnóstico de Conexión"):
        st.write(f"**Entorno:** {'Nube (Cloud)' if IS_CLOUD else 'Local (PC)'}")
        st.write(f"**Librería GSheets:** {'✅ Instalada' if HAS_GSHEETS else '❌ No encontrada'}")
        if not IS_CLOUD:
            st.write(f"**Archivo DB:** `{os.path.abspath(DB_PATH)}`")
            st.write(f"**Existe:** {'✅ Sí' if os.path.exists(DB_PATH) else '❌ No'}")
        if IS_CLOUD:
            gconn = get_gsheets_connection()
            if gconn:
                st.success("✅ Conexión con Google Sheets establecida")
            else:
                st.error("❌ No se pudo conectar con Google Sheets.")

else:
    # ─────────────────────────────────────────────
    # SIDEBAR
    # ─────────────────────────────────────────────
    with st.sidebar:
        st.title("SERVICIOS DE LOGÍSTICA")
        st.write(f"👤 Usuario: {st.session_state.user}")
        st.write(f"🏠 Sucursal: {st.session_state.sucursal}")

        if st.session_state.rol == "Administrador":
            st.divider()
            st.subheader("Configuración de Vista")
            suc_ver = st.selectbox("📍 Sucursal a Gestionar", ["Todas", "Sucursal A", "Sucursal B"])
            st.session_state.suc_ver = suc_ver
        else:
            st.session_state.suc_ver = st.session_state.sucursal
            st.info(f"📍 Sucursal: {st.session_state.sucursal}")

        if st.button("Cerrar Sesión"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    # ─────────────────────────────────────────────
    # ENCABEZADO Y TABS
    # ─────────────────────────────────────────────
    st.markdown("<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: center; color: #8DB600 !important;'>📍 Gestionando: {st.session_state.suc_ver}</h3>", unsafe_allow_html=True)

    titulos = ["📋 CARGAS", "🗺️ MAPA", "📊 HISTORIAL", "👷 PERSONAL", "⛽ GASTOS", "💰 BALANCE"]
    if st.session_state.rol == "Administrador":
        titulos += ["📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]

    tabs = st.tabs(titulos)

    # ─────────────────────────────────────────────
    # PESTAÑA 0: CARGAS
    # ─────────────────────────────────────────────
    with tabs[0]:
        st.header("Registro de Movimiento")

        if st.session_state.suc_ver != "Todas":
            v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal=?", (st.session_state.suc_ver,))]
        else:
            v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos")]

        mov        = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_buscar = "En Playa" if mov == "Entregado" else "En Calle"

        if st.session_state.suc_ver == "Todas":
            u_dispo = [r[0] for r in run_query("SELECT nro_unit FROM stock_playa WHERE estado=?", (est_buscar,))]
        else:
            u_dispo = [r[0] for r in run_query("SELECT nro_unit FROM stock_playa WHERE estado=? AND sucursal=?", (est_buscar, st.session_state.suc_ver))]

        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            with c1:
                f_cli    = st.text_input("Cliente / Obra")
                f_dest   = st.text_input("Dirección de Entrega")
                f_tipo_c = st.selectbox("Contrato", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km     = st.number_input("Km", min_value=0.0)
            with c2:
                f_units = st.multiselect("Nº Unidades", u_dispo)
                f_pat   = st.selectbox("Vehículo", v_list if v_list else ["Sin Patente"])
                f_prec  = st.number_input("Precio Unitario ($)", min_value=0.0)
                f_pago  = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])

            if st.form_submit_button("GUARDAR"):
                if f_units and (f_cli or mov == "Retirado"):
                    lat, lon = None, None
                    if mov == "Entregado":
                        try:
                            location = geolocator.geocode(f"{f_dest}, Cordoba, Argentina", timeout=5)
                            if location:
                                lat, lon = location.latitude, location.longitude
                        except Exception:
                            pass

                    fecha_h = datetime.now().strftime("%d/%m/%Y %H:%M")
                    str_u   = ", ".join(f_units)
                    total_v = len(f_units) * f_prec
                    nuevo_e = "En Calle" if mov == "Entregado" else "En Playa"

                    run_query(
                        """INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov,
                           unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total,
                           estado_pago, lat, lon, sucursal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (fecha_h, f_cli, f_pat, f_dest, mov, str_u, len(f_units),
                         f_tipo_c, f_km, f_prec, total_v, f_pago, lat, lon, st.session_state.sucursal),
                        commit=True
                    )
                    for unit in f_units:
                        run_query("UPDATE stock_playa SET estado=? WHERE nro_unit=?", (nuevo_e, unit), commit=True)

                    st.success("✅ Guardado correctamente.")
                    time.sleep(1)
                    st.rerun()

    # ─────────────────────────────────────────────
    # PESTAÑA 1: MAPA
    # ─────────────────────────────────────────────
    with tabs[1]:
        st.header("Ubicación de Unidades")

        if st.session_state.suc_ver != "Todas":
            rows_mapa = run_query(
                "SELECT cliente, destino, unidades, lat, lon FROM viajes WHERE tipo_mov='Entregado' AND lat IS NOT NULL AND sucursal=%s",
                (st.session_state.suc_ver,)
            )
            rows_calle = run_query("SELECT nro_unit FROM stock_playa WHERE estado='En Calle' AND sucursal=%s", (st.session_state.suc_ver,))
        else:
            rows_mapa  = run_query("SELECT cliente, destino, unidades, lat, lon FROM viajes WHERE tipo_mov='Entregado' AND lat IS NOT NULL")
            rows_calle = run_query("SELECT nro_unit FROM stock_playa WHERE estado='En Calle'")

        df_mapa = pd.DataFrame(rows_mapa)
        unidades_en_calle = set(r['nro_unit'] for r in rows_calle) if rows_calle else set()

        if not df_mapa.empty:
            m = folium.Map(location=[-31.4135, -64.1810], zoom_start=11)
            for _, row in df_mapa.iterrows():
                units_viaje = [u.strip() for u in str(row['unidades']).split(',')]
                es_activo   = any(u in unidades_en_calle for u in units_viaje)
                folium.Marker(
                    [row['lat'], row['lon']],
                    popup=f"<b>{row['cliente']}</b><br>{row['unidades']}",
                    icon=folium.Icon(color='red' if es_activo else 'orange')
                ).add_to(m)
            st_folium(m, width=1100, height=500)
        else:
            st.info("No hay datos de ubicación para mostrar.")

    # ─────────────────────────────────────────────
    # PESTAÑA 2: HISTORIAL
    # ─────────────────────────────────────────────
    with tabs[2]:
        st.header("Historial")

        rows_h = run_query("SELECT * FROM viajes ORDER BY id DESC")
        df_h = pd.DataFrame(rows_h)

        st.dataframe(df_h, use_container_width=True)
        st.write("---")

        if not df_h.empty:
            id_editar = st.selectbox("ID a corregir", [""] + df_h['id'].astype(str).tolist())
            if id_editar != "":
                viaje_sel = df_h[df_h['id'] == int(id_editar)].iloc[0]
                with st.form("form_edit"):
                    e_cli  = st.text_input("Cliente",   value=viaje_sel['cliente'])
                    e_dest = st.text_input("Dirección", value=viaje_sel['destino'])
                    e_prec = st.number_input("Precio ($)", value=float(viaje_sel['precio_unit']))
                    if st.form_submit_button("APLICAR CAMBIOS"):
                        nuevo_total = viaje_sel['cantidad'] * e_prec
                        run_query(
                            "UPDATE viajes SET cliente=?, destino=?, precio_unit=?, total=? WHERE id=?",
                            (e_cli, e_dest, e_prec, nuevo_total, id_editar), commit=True
                        )
                        st.success("Actualizado")
                        time.sleep(1)
                        st.rerun()

    # ─────────────────────────────────────────────
    # PESTAÑA 3: PERSONAL
    # ─────────────────────────────────────────────
    with tabs[3]:
        st.header("👷 Personal")
        with st.form("personal_f"):
            c1, c2, c3 = st.columns(3)
            p_nom = c1.text_input("Nombre")
            p_tar = c2.text_input("Tarea")
            p_mon = c3.number_input("Pago ($)", min_value=0.0)
            if st.form_submit_button("REGISTRAR PAGO"):
                run_query(
                    "INSERT INTO personal (fecha, nombre, tarea, pago, sucursal) VALUES (?,?,?,?,?)",
                    (datetime.now().strftime("%d/%m/%Y"), p_nom, p_tar, p_mon, st.session_state.sucursal),
                    commit=True
                )
                st.rerun()

        rows_p = run_query("SELECT * FROM personal ORDER BY id DESC")
        df_p = pd.DataFrame(rows_p)
        st.dataframe(df_p, use_container_width=True)

    # ─────────────────────────────────────────────
    # PESTAÑA 4: GASTOS
    # ─────────────────────────────────────────────
    with tabs[4]:
        st.header("⛽ Gastos")
        v_list_g = [r[0] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal=?", (st.session_state.sucursal,))]
        with st.form("gastos_f"):
            c1, c2, c3 = st.columns(3)
            g_pat = c1.selectbox("Vehículo", v_list_g if v_list_g else ["S/P"])
            g_con = c2.selectbox("Concepto", ["Combustible", "Aceite", "Repuestos", "Limpieza", "Otros"])
            g_mon = c3.number_input("Monto ($)", min_value=0.0)
            if st.form_submit_button("CARGAR GASTO"):
                run_query(
                    "INSERT INTO gastos (fecha, patente, concepto, monto, sucursal) VALUES (?,?,?,?,?)",
                    (datetime.now().strftime("%d/%m/%Y"), g_pat, g_con, g_mon, st.session_state.sucursal),
                    commit=True
                )
                st.rerun()

        rows_g = run_query("SELECT * FROM gastos ORDER BY id DESC")
        df_g = pd.DataFrame(rows_g)
        st.dataframe(df_g, use_container_width=True)

    # ─────────────────────────────────────────────
    # PESTAÑA 5: BALANCE
    # ─────────────────────────────────────────────
    with tabs[5]:
        st.header(f"💰 Balance de Caja - {st.session_state.suc_ver}")
        try:
            cond_suc = st.session_state.suc_ver if st.session_state.suc_ver != "Todas" else None

            def get_sum(tabla, col_pago):
                res = run_query(f"SELECT {col_pago}, sucursal FROM {tabla}")
                if not res:
                    return 0.0
                df_temp = pd.DataFrame(res, columns=[col_pago, "sucursal"])
                if cond_suc:
                    df_temp = df_temp[df_temp["sucursal"] == cond_suc]
                return pd.to_numeric(df_temp[col_pago], errors='coerce').sum()

            ingresos       = get_sum("viajes",   "total")
            egresos_pers   = get_sum("personal", "pago")
            egresos_gastos = get_sum("gastos",   "monto")
            neto           = ingresos - (egresos_pers + egresos_gastos)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Ingresos",  f"${ingresos:,.2f}")
            col2.metric("Personal",  f"-${egresos_pers:,.2f}")
            col3.metric("Gastos",    f"-${egresos_gastos:,.2f}")
            col4.metric("NETO",      f"${neto:,.2f}", delta=neto)
        except Exception as e:
            st.info(f"Esperando datos para el balance... ({e})")

    # ─────────────────────────────────────────────
    # PESTAÑAS ADMIN
    # ─────────────────────────────────────────────
    if st.session_state.rol == "Administrador":

        # STOCK
        with tabs[6]:
            st.header("📦 Stock")
            c_input, c_view = st.columns([1, 2])
            with c_input:
                st.subheader("Agregar Unidad")
                tipo_u    = st.selectbox("Tipo", ["Baño Químico", "Contenedor", "Oficina Móvil"])
                nu        = st.text_input("ID Unidad (ej: B01)")
                mo        = st.text_input("Modelo/Marca")
                suc_stock = st.selectbox("Sucursal Asignada", ["Sucursal A", "Sucursal B"])

                if st.button("GUARDAR STOCK", use_container_width=True):
                    if nu:
                        try:
                            run_query("INSERT INTO stock_playa VALUES (?,?,?,?,?)",
                                      (nu, tipo_u, mo, 'En Playa', suc_stock), commit=True)
                            st.success("Agregado")
                            time.sleep(0.5)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

                st.divider()
                st.subheader("Eliminar Unidad")
                try:
                    res_stock = run_query("SELECT nro_unit FROM stock_playa")
                    df_stock_temp = pd.DataFrame(res_stock)
                    if not df_stock_temp.empty:
                        u_borrar = st.selectbox("Seleccionar unidad", [""] + df_stock_temp['nro_unit'].tolist())
                        if st.button("❌ ELIMINAR", use_container_width=True):
                            run_query("DELETE FROM stock_playa WHERE nro_unit=%s", (u_borrar,), commit=True)
                            st.rerun()
                except Exception as e:
                    st.warning(f"No se pudo cargar el listado: {e}")

            with c_view:
                st.subheader(f"Inventario - {st.session_state.suc_ver}")
                rows_s = run_query("SELECT * FROM stock_playa")
                df_s = pd.DataFrame(rows_s)
                st.dataframe(df_s, use_container_width=True, height=500)

        # VEHÍCULOS
        with tabs[7]:
            st.header("🚛 Vehículos")
            pa      = st.text_input("Patente Camión").upper()
            suc_veh = st.selectbox("Sucursal Vehículo", ["Sucursal A", "Sucursal B"])
            if st.button("CARGAR CAMIÓN"):
                if pa:
                    run_query("INSERT INTO vehiculos VALUES (?,?,?)", (pa, "Unidad", suc_veh), commit=True)
                    st.rerun()

            rows_v = run_query("SELECT * FROM vehiculos")
            df_v = pd.DataFrame(rows_v)
            st.table(df_v)

        # USUARIOS
        with tabs[8]:
            st.header("👥 Gestión de Usuarios")
            with st.form("new_user"):
                c1, c2, c3, c4 = st.columns(4)
                un       = c1.text_input("Nuevo Usuario")
                pn       = c2.text_input("Clave", type="password")
                rol_new  = c3.selectbox("Rol", ["Operador", "Administrador"])
                suc_user = c4.selectbox("Sucursal", ["Sucursal A", "Sucursal B", "Todas"])
                if st.form_submit_button("CREAR USUARIO"):
                    if un and pn:
                        run_query("INSERT INTO usuarios VALUES (?,?,?,?)",
                                  (un, hash_password(pn), rol_new, suc_user), commit=True)
                        st.success(f"Usuario {un} creado.")
                        time.sleep(1)
                        st.rerun()

            st.subheader("Usuarios Existentes")
            res_users = run_query("SELECT \"user\", rol, sucursal FROM usuarios")
            df_users = pd.DataFrame(res_users)
            st.dataframe(df_users, use_container_width=True)

            u_del = st.selectbox(
                "Seleccionar usuario para eliminar",
                [""] + df_users['user'].tolist() if not df_users.empty else [""]
            )
            if st.button("🗑️ ELIMINAR USUARIO"):
                if u_del and u_del != "marcelo":
                    run_query("DELETE FROM usuarios WHERE user=?", (u_del,), commit=True)
                    st.success(f"Usuario {u_del} eliminado.")
                    time.sleep(1)
                    st.rerun()
