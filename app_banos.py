import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import io
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import time

# --- CONFIGURACIÓN ESTÉTICA ---
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

# --- BASE DE DATOS UNIFICADA CON CACHÉ ---
@st.cache_resource
def get_db_connection():
    return sqlite3.connect('gestion_banos.db', check_same_thread=False)

def run_query(query, params=(), commit=False):
    conn = get_db_connection()
    c = conn.cursor()
    # Retry logic for locked database
    for _ in range(5):
        try:
            c.execute(query, params)
            if commit:
                conn.commit()
                return True
            return c.fetchall()
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(0.1)
            else:
                raise e
        except Exception as e:
            raise e
    raise sqlite3.OperationalError("Database is locked after multiple retries")

# Inicialización de Tablas
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    try:
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
        conn.commit()
        
        # Crear admin por defecto si no existe
        try:
            c.execute("INSERT INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
            conn.commit()
        except sqlite3.IntegrityError:
            pass
    except sqlite3.OperationalError:
        pass # Si falla por lock, probablemente ya está inicializado

# Ejecutar inicialización al arranque
init_db()

# --- CONFIGURACIÓN DE GPS ---
geolocator = Nominatim(user_agent="servicios_logistica_v1_fix")

# --- LOGIN ---
if 'login' not in st.session_state:
    st.session_state.login = False

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,1,1])
    with c2:
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.button("INGRESAR", use_container_width=True):
            res = run_query("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
            if res:
                st.session_state.login, st.session_state.rol = True, res[0][0]
                st.rerun()
            else: 
                st.error("Acceso incorrecto")
else:
    with st.sidebar:
        st.title("SERVICIOS DE LOGÍSTICA")
        st.write(f"👤 Usuario: {u if 'u' in locals() else 'Sesión Activa'}")
        if st.button("Cerrar Sesión"):
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
        v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos")]
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_buscar = "En Playa" if mov == "Entregado" else "En Calle"
        
        # Filtrar unidades disponibles
        try:
            u_dispo = [r[0] for r in run_query(f"SELECT nro_unit FROM stock_playa WHERE estado='{est_buscar}'")]
        except Exception:
            u_dispo = []
            
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
                if f_units and (f_cli or mov == "Retirado"):
                    lat, lon = None, None
                    # Intentar Geolocalización
                    if mov == "Entregado":
                        try:
                            busqueda = f"{f_dest}, Cordoba, Argentina"
                            location = geolocator.geocode(busqueda, timeout=5)
                            if location: 
                                lat, lon = location.latitude, location.longitude
                            else:
                                st.warning(f"⚠️ No se pudo geolocalizar '{f_dest}'. Se guardará sin mapa.")
                        except (GeocoderTimedOut, GeocoderServiceError) as e:
                            st.warning(f"⚠️ Error de conexión con mapa: {e}")
                        except Exception as e:
                            st.warning(f"⚠️ Error inesperado: {e}")

                    fecha_h = datetime.now().strftime("%d/%m/%Y %H:%M")
                    str_u = ", ".join(f_units)
                    total_v = len(f_units) * f_prec
                    nuevo_e = "En Calle" if mov == "Entregado" else "En Playa"
                    
                    run_query("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                                 unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago, lat, lon) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (fecha_h, f_cli, f_pat, f_dest, mov, str_u, len(f_units), f_tipo_c, f_km, f_prec, total_v, f_pago, lat, lon), commit=True)
                    
                    for unit in f_units:
                        run_query("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_e, unit), commit=True)
                    
                    st.success("✅ Guardado correctamente.")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Falta seleccionar Unidades o Cliente")

    # --- PESTAÑA 1: MAPA ---
    with tabs[1]:
        st.header("Ubicación de Unidades")
        conn = get_db_connection()
        try:
            df_mapa = pd.read_sql_query("SELECT cliente, destino, unidades, lat, lon FROM viajes WHERE tipo_mov = 'Entregado' AND lat IS NOT NULL", conn)
        except:
            df_mapa = pd.DataFrame()

        try:
            unidades_en_calle = set([r[0] for r in run_query("SELECT nro_unit FROM stock_playa WHERE estado='En Calle'")])
        except:
            unidades_en_calle = set()
        
        if not df_mapa.empty:
            m = folium.Map(location=[-31.4135, -64.1810], zoom_start=11)
            puntos_activos = 0
            puntos_historial = 0

            for _, row in df_mapa.iterrows():
                if row['unidades']:
                    units_viaje = [u.strip() for u in str(row['unidades']).split(',')]
                    
                    # Si al menos UNA unidad sigue en calle, es ACTIVO
                    es_activo = any(u in unidades_en_calle for u in units_viaje)
                    
                    color_mk = 'red' if es_activo else 'orange'
                    icon_mk = 'info-sign' if es_activo else 'time' 
                    estado_txt = "ACTIVO" if es_activo else "HISTORIAL (Retirado)"
                    
                    folium.Marker(
                        [row['lat'], row['lon']], 
                        popup=f"<b>{row['cliente']}</b><br>{row['destino']}<br>{row['unidades']}<br><i>{estado_txt}</i>",
                        icon=folium.Icon(color=color_mk, icon=icon_mk)
                    ).add_to(m)
                    
                    if es_activo: puntos_activos += 1
                    else: puntos_historial += 1

            st_folium(m, width=1100, height=500)
            st.caption(f"🔴 Rojo: Activos ({puntos_activos}) | 🟠 Naranja: Historial ({puntos_historial})")
        else:
            st.info("No hay datos de geolocalización registrados.")

    # --- PESTAÑA 2: HISTORIAL + EDITOR ---
    with tabs[2]:
        st.header("Historial")
        conn = get_db_connection()
        try:
            df_h = pd.read_sql_query("SELECT * FROM viajes ORDER BY id DESC", conn)
        except:
            df_h = pd.DataFrame()
            
        st.dataframe(df_h, use_container_width=True)
        st.write("---")
        if not df_h.empty:
            id_editar = st.selectbox("ID a corregir", [""] + df_h['id'].astype(str).tolist())
            if id_editar != "":
                viaje_sel = df_h[df_h['id'] == int(id_editar)].iloc[0]
                with st.form("form_edit"):
                    e_cli = st.text_input("Cliente", value=viaje_sel['cliente'])
                    e_dest = st.text_input("Dirección", value=viaje_sel['destino'])
                    e_prec = st.number_input("Precio ($)", value=float(viaje_sel['precio_unit']))
                    if st.form_submit_button("APLICAR CAMBIOS"):
                        nuevo_total = viaje_sel['cantidad'] * e_prec
                        run_query("UPDATE viajes SET cliente=?, destino=?, precio_unit=?, total=? WHERE id=?", (e_cli, e_dest, e_prec, nuevo_total, id_editar), commit=True)
                        st.success("Actualizado")
                        time.sleep(1)
                        st.rerun()

    # --- PESTAÑA 3: PERSONAL ---
    with tabs[3]:
        st.header("👷 Personal")
        with st.form("personal_f"):
            c1, c2, c3 = st.columns(3)
            p_nom = c1.text_input("Nombre")
            p_tar = c2.text_input("Tarea")
            p_mon = c3.number_input("Pago ($)", min_value=0.0)
            if st.form_submit_button("REGISTRAR PAGO"):
                run_query("INSERT INTO personal (fecha, nombre, tarea, pago) VALUES (?,?,?,?)", (datetime.now().strftime("%d/%m/%Y"), p_nom, p_tar, p_mon), commit=True)
                st.rerun()
        conn = get_db_connection()
        st.dataframe(pd.read_sql_query("SELECT * FROM personal ORDER BY id DESC", conn), use_container_width=True)

    # --- PESTAÑA 4: GASTOS ---
    with tabs[4]:
        st.header("⛽ Gastos")
        v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos")]
        with st.form("gastos_f"):
            c1, c2, c3 = st.columns(3)
            g_pat = c1.selectbox("Vehículo", v_list if v_list else ["S/P"])
            g_con = c2.selectbox("Concepto", ["Combustible", "Aceite", "Repuestos", "Limpieza", "Otros"])
            g_mon = c3.number_input("Monto ($)", min_value=0.0)
            if st.form_submit_button("CARGAR GASTO"):
                run_query("INSERT INTO gastos (fecha, patente, concepto, monto) VALUES (?,?,?,?)", (datetime.now().strftime("%d/%m/%Y"), g_pat, g_con, g_mon), commit=True)
                st.rerun()
        conn = get_db_connection()
        st.dataframe(pd.read_sql_query("SELECT * FROM gastos ORDER BY id DESC", conn), use_container_width=True)

    # --- PESTAÑA 5: BALANCE ---
    with tabs[5]:
        st.header("💰 Balance de Caja")
        conn = get_db_connection()
        try:
            ingresos = pd.read_sql_query("SELECT SUM(total) FROM viajes", conn).iloc[0,0] or 0
            egresos_pers = pd.read_sql_query("SELECT SUM(pago) FROM personal", conn).iloc[0,0] or 0
            egresos_gastos = pd.read_sql_query("SELECT SUM(monto) FROM gastos", conn).iloc[0,0] or 0
            neto = ingresos - (egresos_pers + egresos_gastos)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Ingresos", f"${ingresos:,.2f}")
            col2.metric("Personal", f"-${egresos_pers:,.2f}")
            col3.metric("Gastos", f"-${egresos_gastos:,.2f}")
            col4.metric("NETO", f"${neto:,.2f}", delta=neto)
        except:
             st.info("No hay datos suficientes para el balance.")

    # --- PESTAÑAS ADMIN ---
    if st.session_state.rol == "Administrador":
        with tabs[6]: # STOCK
            st.header("📦 Stock")
            
            c_input, c_view = st.columns([1, 2])
            
            with c_input:
                st.subheader("Agregar Unidad")
                tipo_u = st.selectbox("Tipo", ["Baño Químico", "Contenedor", "Oficina Móvil"])
                nu = st.text_input("ID Unidad (ej: B01)")
                mo = st.text_input("Modelo/Marca")
                
                if st.button("GUARDAR STOCK", use_container_width=True):
                    if nu:
                        try:
                            # Check if exists first to avoid integrity error causing issues
                            run_query("INSERT INTO stock_playa VALUES (?,?,?, 'En Playa')", (nu, tipo_u, mo), commit=True)
                            st.success("Agregado"); time.sleep(0.5); st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ese ID de unidad ya existe.")
                        except Exception as e:
                            st.error(f"Error: {e}")
                    else:
                        st.error("Falta ID")
                
                st.divider()
                st.subheader("Eliminar Unidad")
                conn = get_db_connection()
                try:
                    df_stock_temp = pd.read_sql_query("SELECT nro_unit FROM stock_playa", conn)
                    u_borrar = st.selectbox("Seleccionar unidad", [""] + df_stock_temp['nro_unit'].tolist())
                    if st.button("❌ ELIMINAR", use_container_width=True):
                        if u_borrar:
                            run_query("DELETE FROM stock_playa WHERE nro_unit=?", (u_borrar,), commit=True)
                            st.rerun()
                except:
                    pass

            with c_view:
                st.subheader("Inventario Actual")
                conn = get_db_connection()
                df_stock = pd.read_sql_query("SELECT * FROM stock_playa", conn)
                st.dataframe(df_stock, use_container_width=True, height=500)

        with tabs[7]: # VEHÍCULOS
            st.header("🚛 Vehículos")
            pa = st.text_input("Patente Camión").upper()
            if st.button("CARGAR CAMIÓN"):
                if pa:
                    try:
                        run_query("INSERT INTO vehiculos VALUES (?,?)", (pa, "Unidad"), commit=True)
                        st.rerun()
                    except sqlite3.IntegrityError:
                        pass
            conn = get_db_connection()
            st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

        with tabs[8]: # USUARIOS
            st.header("👥 Gestión de Usuarios")
            
            # Crear Usuario
            with st.form("new_user"):
                c1, c2, c3 = st.columns(3)
                un = c1.text_input("Nuevo Usuario")
                pn = c2.text_input("Clave", type="password")
                rol = c3.selectbox("Rol", ["Operador", "Administrador"])
                if st.form_submit_button("CREAR USUARIO"):
                    if un and pn:
                        try:
                            run_query("INSERT INTO usuarios VALUES (?,?,?)", (un, pn, rol), commit=True)
                            st.success(f"Usuario {un} creado.")
                            time.sleep(1)
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("El usuario ya existe.")
                    else:
                        st.error("Complete usuario y clave.")
            
            st.divider()
            # Listar y Borrar Usuarios
            st.subheader("Usuarios Existentes")
            conn = get_db_connection()
            df_users = pd.read_sql_query("SELECT user, rol FROM usuarios", conn)
            st.dataframe(df_users, use_container_width=True)
            
            u_del = st.selectbox("Seleccionar usuario para eliminar", [""] + df_users['user'].tolist())
            if st.button("🗑️ ELIMINAR USUARIO"):
                if u_del and u_del != "admin":
                    run_query("DELETE FROM usuarios WHERE user=?", (u_del,), commit=True)
                    st.success(f"Usuario {u_del} eliminado.")
                    time.sleep(1)
                    st.rerun()
                elif u_del == "admin":
                    st.error("No se puede eliminar al administrador principal.")
