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
import os

try:
    from streamlit_gsheets import GSheetsConnection
    HAS_GSHEETS = True
except ImportError:
    HAS_GSHEETS = False

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
# --- BASE DE DATOS UNIFICADA (SQLite Local o GSheets Cloud) ---
IS_CLOUD = 'STREAMLIT_RUNTIME_ENV' in os.environ or os.path.exists('.streamlit/secrets.toml')

@st.cache_resource
def get_db_connection():
    if not IS_CLOUD:
        return sqlite3.connect('gestion_banos.db', check_same_thread=False)
    else:
        if not HAS_GSHEETS:
            st.error("❌ Error: La librería 'st-gsheets-connection' no está instalada. Ejecuta 'pip install st-gsheets-connection' en tu terminal.")
            return None
        # En la nube usamos st.connection para Google Sheets
        try:
            return st.connection("gsheets", type=GSheetsConnection)
        except Exception:
            st.error("⚠️ No se encontró la configuración de Google Sheets en Secrets.")
            return None

def run_query(query, params=(), commit=False):
    if not IS_CLOUD:
        conn = get_db_connection()
        c = conn.cursor()
        for _ in range(5):
            try:
                c.execute(query, params)
                if commit:
                    conn.commit()
                    return True
                return c.fetchall()
            except sqlite3.OperationalError as e:
                if "locked" in str(e): time.sleep(0.1)
                else: raise e
        raise sqlite3.OperationalError("Database is locked")
    else:
        # LOGICA PARA GOOGLE SHEETS (Modo simplificado usando st.connection)
        conn = get_db_connection()
        if not conn: return []

        q_lower = query.lower()
        tablas = ["usuarios", "vehiculos", "viajes", "stock_playa", "personal", "gastos"]
        tabla_target = next((t for t in tablas if t in q_lower), None)
        
        if not tabla_target:
            return []

        try:
            df = conn.read(worksheet=tabla_target)
            
            if "select" in q_lower:
                df_res = df.copy()
                # Filtrado simple
                if "where" in q_lower and params:
                    if tabla_target == "usuarios" and "user=?" in q_lower.replace(" ",""):
                        df_res = df_res[(df_res['user'] == str(params[0])) & (df_res['password'] == str(params[1]))]
                    elif "sucursal=?" in q_lower.replace(" ",""):
                        df_res = df_res[df_res['sucursal'] == params[0]]
                    elif "estado=?" in q_lower.replace(" ",""):
                        df_res = df_res[df_res['estado'] == params[0]]
                
                # Columnas solicitadas
                cols_str = q_lower.split("select")[1].split("from")[0].strip()
                if cols_str != "*" and "," in cols_str:
                    cols_req = [c.strip() for c in cols_str.split(",")]
                    valid_cols = [c for c in cols_req if c in df_res.columns]
                    return [tuple(x) for x in df_res[valid_cols].values]
                
                return [tuple(x) for x in df_res.values]

            if commit:
                if "insert" in q_lower:
                    if tabla_target in ["viajes", "personal", "gastos"]:
                        next_id = int(df['id'].max() + 1) if not df.empty and 'id' in df.columns else 1
                        params = (next_id,) + params
                    
                    if len(params) == len(df.columns):
                        new_row = pd.DataFrame([params], columns=df.columns)
                        df = pd.concat([df, new_row], ignore_index=True)
                    else:
                        st.error(f"Error: Columnas no coinciden en {tabla_target}")
                        return False
                elif "update" in q_lower:
                    if "stock_playa" in q_lower:
                        df.loc[df['nro_unit'] == params[1], 'estado'] = params[0]
                    elif "viajes" in q_lower:
                        df.loc[df['id'] == int(params[4]), ['cliente', 'destino', 'precio_unit', 'total']] = params[:4]
                elif "delete" in q_lower:
                    if "stock_playa" in q_lower:
                        df = df[df['nro_unit'] != params[0]]
                    elif "usuarios" in q_lower:
                        df = df[df['user'] != params[0]]
                
                try:
                    conn.update(worksheet=tabla_target, data=df)
                except Exception as e:
                    if "UnsupportedOperationError" in str(type(e).__name__):
                        st.error("🔒 Error de Escritura: La conexión actual es de 'Solo Lectura'. Para guardar datos en la nube necesitas configurar una 'Service Account' en los Secrets de Streamlit.")
                    else:
                        st.error(f"Error al guardar en GSheets: {e}")
                    return False
                return True
        except Exception as e:
            if "Worksheet not found" in str(e): return []
            st.error(f"Error en GSheets ({tabla_target}): {e}")
            return []

# Inicialización de Tablas
# Inicialización de Tablas
def init_db():
    if not IS_CLOUD:
        conn = get_db_connection()
        c = conn.cursor()
        tablas_cols = {
            "usuarios": ["user", "password", "rol", "sucursal"],
            "vehiculos": ["patente", "modelo", "sucursal"],
            "viajes": ["id", "fecha", "cliente", "patente", "destino", "tipo_mov", "unidades", "cantidad", "tipo_contrato", "km_entrega", "precio_unit", "total", "estado_pago", "lat", "lon", "sucursal"],
            "stock_playa": ["nro_unit", "tipo", "modelo", "estado", "sucursal"],
            "personal": ["id", "fecha", "nombre", "tarea", "pago", "sucursal"],
            "gastos": ["id", "fecha", "patente", "concepto", "monto", "sucursal"]
        }
        
        # Crear tablas si no existen
        c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT, sucursal TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT, sucursal TEXT)')
        c.execute('''CREATE TABLE IF NOT EXISTS viajes 
                     (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
                      destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER, 
                      tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL, 
                      estado_pago TEXT, lat REAL, lon REAL, sucursal TEXT)''')
        c.execute('CREATE TABLE IF NOT EXISTS stock_playa (nro_unit TEXT PRIMARY KEY, tipo TEXT, modelo TEXT, estado TEXT, sucursal TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS personal (id INTEGER PRIMARY KEY, fecha TEXT, nombre TEXT, tarea TEXT, pago REAL, sucursal TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS gastos (id INTEGER PRIMARY KEY, fecha TEXT, patente TEXT, concepto TEXT, monto REAL, sucursal TEXT)')
        
        # MIGRACIÓN: Agregar columna sucursal si falta en cada tabla
        for tabla, cols in tablas_cols.items():
            try:
                c.execute(f"PRAGMA table_info({tabla})")
                cols_actuales = [row[1] for row in c.fetchall()]
                if "sucursal" not in cols_actuales:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN sucursal TEXT DEFAULT 'Sucursal A'")
                    conn.commit()
            except Exception: pass
        
        try:
            c.execute("INSERT INTO usuarios VALUES ('admin', 'admin123', 'Administrador', 'Todas')")
            conn.commit()
        except sqlite3.IntegrityError: pass
    else:
        # Inicialización en Google Sheets
        conn = get_db_connection()
        if not conn: return
        
        tablas = {
            "usuarios": ["user", "password", "rol", "sucursal"],
            "vehiculos": ["patente", "modelo", "sucursal"],
            "viajes": ["id", "fecha", "cliente", "patente", "destino", "tipo_mov", "unidades", "cantidad", "tipo_contrato", "km_entrega", "precio_unit", "total", "estado_pago", "lat", "lon", "sucursal"],
            "stock_playa": ["nro_unit", "tipo", "modelo", "estado", "sucursal"],
            "personal": ["id", "fecha", "nombre", "tarea", "pago", "sucursal"],
            "gastos": ["id", "fecha", "patente", "concepto", "monto", "sucursal"]
        }
        for nombre, cols in tablas.items():
            try:
                df_actual = conn.read(worksheet=nombre)
                # MIGRACIÓN: Si faltan columnas (como 'sucursal'), las agregamos
                missing = [c for c in cols if c not in df_actual.columns]
                if missing:
                    for c in missing:
                        df_actual[c] = "Todas" if nombre == "usuarios" else "Sucursal A"
                    conn.update(worksheet=nombre, data=df_actual)
            except Exception:
                # Si no existe, crear con cabeceras
                df_init = pd.DataFrame(columns=cols)
                if nombre == "usuarios":
                    df_init.loc[0] = ["admin", "admin123", "Administrador", "Todas"]
                conn.update(worksheet=nombre, data=df_init)

# Ejecutar inicialización al arranque
try:
    init_db()
except Exception as e:
    if IS_CLOUD:
        st.warning(f"⚠️ Nota: No se pudo inicializar/verificar Google Sheets (Posible modo Solo Lectura). Detalles: {e}")
    else:
        st.error(f"Error al inicializar base de datos: {e}")

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
            res = run_query("SELECT rol, sucursal FROM usuarios WHERE user=? AND password=?", (u, p))
            if res:
                st.session_state.login = True
                st.session_state.rol = res[0][0]
                st.session_state.sucursal = res[0][1]
                st.session_state.user = u
                st.rerun()
            else: 
                st.error("Acceso incorrecto")
else:
    with st.sidebar:
        st.title("SERVICIOS DE LOGÍSTICA")
        st.write(f"👤 Usuario: {st.session_state.user}")
        st.write(f"🏠 Sucursal: {st.session_state.sucursal}")
        
        # Filtro de sucursal para Admin
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

    # Definición de Pestañas
    st.markdown(f"<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: center; color: #8DB600 !important;'>📍 Gestionando: {st.session_state.suc_ver}</h3>", unsafe_allow_html=True)
    
    titulos = ["📋 CARGAS", "🗺️ MAPA", "📊 HISTORIAL", "👷 PERSONAL", "⛽ GASTOS", "💰 BALANCE"]
    if st.session_state.rol == "Administrador":
        titulos += ["📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]
    
    tabs = st.tabs(titulos)

    # --- PESTAÑA 0: CARGAS ---
    with tabs[0]:
        st.header("Registro de Movimiento")
        v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal = ?", (st.session_state.suc_ver,))] if st.session_state.suc_ver != "Todas" else [r[0] for r in run_query("SELECT patente FROM vehiculos")]
        
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_buscar = "En Playa" if mov == "Entregado" else "En Calle"
        
        # Filtrar unidades disponibles por sucursal
        q_units = "SELECT nro_unit FROM stock_playa WHERE estado=? AND sucursal=?"
        p_units = (est_buscar, st.session_state.suc_ver)
        if st.session_state.suc_ver == "Todas":
            q_units = "SELECT nro_unit FROM stock_playa WHERE estado=?"
            p_units = (est_buscar,)
            
        try:
            u_dispo = [r[0] for r in run_query(q_units, p_units)]
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
                    if mov == "Entregado":
                        try:
                            busqueda = f"{f_dest}, Cordoba, Argentina"
                            location = geolocator.geocode(busqueda, timeout=5)
                            if location: lat, lon = location.latitude, location.longitude
                        except Exception: pass

                    fecha_h = datetime.now().strftime("%d/%m/%Y %H:%M")
                    str_u = ", ".join(f_units)
                    total_v = len(f_units) * f_prec
                    nuevo_e = "En Calle" if mov == "Entregado" else "En Playa"
                    
                    run_query("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                                 unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago, lat, lon, sucursal) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (fecha_h, f_cli, f_pat, f_dest, mov, str_u, len(f_units), f_tipo_c, f_km, f_prec, total_v, f_pago, lat, lon, st.session_state.sucursal), commit=True)
                    
                    for unit in f_units:
                        run_query("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_e, unit), commit=True)
                    
                    st.success("✅ Guardado correctamente.")
                    time.sleep(1); st.rerun()

    # --- PESTAÑA 1: MAPA ---
    with tabs[1]:
        st.header("Ubicación de Unidades")
        q_mapa = "SELECT cliente, destino, unidades, lat, lon FROM viajes WHERE tipo_mov = 'Entregado' AND lat IS NOT NULL"
        q_calle = "SELECT nro_unit FROM stock_playa WHERE estado='En Calle'"
        
        if st.session_state.suc_ver != "Todas":
            q_mapa += f" AND sucursal='{st.session_state.suc_ver}'"
            q_calle += f" AND sucursal='{st.session_state.suc_ver}'"
            
        conn = get_db_connection()
        try:
            df_mapa = pd.read_sql_query(q_mapa, conn) if not IS_CLOUD else pd.DataFrame(run_query(q_mapa)) # Simplificado
            if IS_CLOUD: df_mapa.columns = ["cliente", "destino", "unidades", "lat", "lon"]
            unidades_en_calle = set([r[0] for r in run_query(q_calle)])
        except:
            df_mapa, unidades_en_calle = pd.DataFrame(), set()
        
        if not df_mapa.empty:
            m = folium.Map(location=[-31.4135, -64.1810], zoom_start=11)
            for _, row in df_mapa.iterrows():
                units_viaje = [u.strip() for u in str(row['unidades']).split(',')]
                es_activo = any(u in unidades_en_calle for u in units_viaje)
                folium.Marker([row['lat'], row['lon']], 
                               popup=f"<b>{row['cliente']}</b><br>{row['unidades']}",
                               icon=folium.Icon(color='red' if es_activo else 'orange')).add_to(m)
            st_folium(m, width=1100, height=500)
        else:
            st.info("No hay datos para mostrar.")

    # --- PESTAÑA 2: HISTORIAL ---
    with tabs[2]:
        st.header("Historial")
        q_hist = "SELECT * FROM viajes"
        if st.session_state.suc_ver != "Todas":
            q_hist += f" WHERE sucursal='{st.session_state.suc_ver}'"
        q_hist += " ORDER BY id DESC"
        
        try:
            df_h = pd.DataFrame(run_query(q_hist))
            if not df_h.empty:
                df_h.columns = ["id", "fecha", "cliente", "patente", "destino", "tipo_mov", "unidades", "cantidad", "tipo_contrato", "km_entrega", "precio_unit", "total", "estado_pago", "lat", "lon", "sucursal"]
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
                run_query("INSERT INTO personal (fecha, nombre, tarea, pago, sucursal) VALUES (?,?,?,?,?)", 
                          (datetime.now().strftime("%d/%m/%Y"), p_nom, p_tar, p_mon, st.session_state.sucursal), commit=True)
                st.rerun()
        
        q_pers = "SELECT * FROM personal"
        if st.session_state.suc_ver != "Todas":
            q_pers += f" WHERE sucursal='{st.session_state.suc_ver}'"
        q_pers += " ORDER BY id DESC"
        
        df_p = pd.DataFrame(run_query(q_pers))
        if not df_p.empty:
            df_p.columns = ["id", "fecha", "nombre", "tarea", "pago", "sucursal"]
        st.dataframe(df_p, use_container_width=True)

    # --- PESTAÑA 4: GASTOS ---
    with tabs[4]:
        st.header("⛽ Gastos")
        v_list = [r[0] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal = ?", (st.session_state.sucursal,))]
        with st.form("gastos_f"):
            c1, c2, c3 = st.columns(3)
            g_pat = c1.selectbox("Vehículo", v_list if v_list else ["S/P"])
            g_con = c2.selectbox("Concepto", ["Combustible", "Aceite", "Repuestos", "Limpieza", "Otros"])
            g_mon = c3.number_input("Monto ($)", min_value=0.0)
            if st.form_submit_button("CARGAR GASTO"):
                run_query("INSERT INTO gastos (fecha, patente, concepto, monto, sucursal) VALUES (?,?,?,?,?)", 
                          (datetime.now().strftime("%d/%m/%Y"), g_pat, g_con, g_mon, st.session_state.sucursal), commit=True)
                st.rerun()
        
        q_gast = "SELECT * FROM gastos"
        if st.session_state.suc_ver != "Todas":
            q_gast += f" WHERE sucursal='{st.session_state.suc_ver}'"
        q_gast += " ORDER BY id DESC"
        
        df_g = pd.DataFrame(run_query(q_gast))
        if not df_g.empty:
            df_g.columns = ["id", "fecha", "patente", "concepto", "monto", "sucursal"]
        st.dataframe(df_g, use_container_width=True)

    # --- PESTAÑA 5: BALANCE ---
    with tabs[5]:
        st.header(f"💰 Balance de Caja - {st.session_state.suc_ver}")
        try:
            cond_suc = st.session_state.suc_ver if st.session_state.suc_ver != "Todas" else None
            
            # Obtener datos y filtrar en memoria para mayor compatibilidad
            def get_sum(tabla, col_pago):
                res = run_query(f"SELECT {col_pago}, sucursal FROM {tabla}")
                if not res: return 0.0
                df_temp = pd.DataFrame(res, columns=[col_pago, "sucursal"])
                if cond_suc:
                    df_temp = df_temp[df_temp["sucursal"] == cond_suc]
                return pd.to_numeric(df_temp[col_pago], errors='coerce').sum()

            ingresos = get_sum("viajes", "total")
            egresos_pers = get_sum("personal", "pago")
            egresos_gastos = get_sum("gastos", "monto")
            
            neto = ingresos - (egresos_pers + egresos_gastos)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Ingresos", f"${ingresos:,.2f}")
            col2.metric("Personal", f"-${egresos_pers:,.2f}")
            col3.metric("Gastos", f"-${egresos_gastos:,.2f}")
            col4.metric("NETO", f"${neto:,.2f}", delta=neto)
        except Exception as e:
             st.info(f"Esperando datos para el balance... ({e})")

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
                suc_stock = st.selectbox("Sucursal Asignada", ["Sucursal A", "Sucursal B"])
                
                if st.button("GUARDAR STOCK", use_container_width=True):
                    if nu:
                        try:
                            run_query("INSERT INTO stock_playa VALUES (?,?,?,?,?)", (nu, tipo_u, mo, 'En Playa', suc_stock), commit=True)
                            st.success("Agregado"); time.sleep(0.5); st.rerun()
                        except Exception as e: st.error(f"Error: {e}")
                
                st.divider()
                st.subheader("Eliminar Unidad")
                try:
                    df_stock_temp = pd.DataFrame(run_query("SELECT nro_unit FROM stock_playa"))
                    if not df_stock_temp.empty:
                        u_borrar = st.selectbox("Seleccionar unidad", [""] + df_stock_temp[0].tolist())
                        if st.button("❌ ELIMINAR", use_container_width=True):
                            run_query("DELETE FROM stock_playa WHERE nro_unit=?", (u_borrar,), commit=True)
                            st.rerun()
                except: pass

            with c_view:
                st.subheader(f"Inventario - {st.session_state.suc_ver}")
                q_stock = "SELECT * FROM stock_playa"
                if st.session_state.suc_ver != "Todas":
                    q_stock += f" WHERE sucursal='{st.session_state.suc_ver}'"
                
                df_s = pd.DataFrame(run_query(q_stock))
                if not df_s.empty: df_s.columns = ["nro_unit", "tipo", "modelo", "estado", "sucursal"]
                st.dataframe(df_s, use_container_width=True, height=500)

        with tabs[7]: # VEHÍCULOS
            st.header("🚛 Vehículos")
            pa = st.text_input("Patente Camión").upper()
            suc_veh = st.selectbox("Sucursal Vehículo", ["Sucursal A", "Sucursal B"])
            if st.button("CARGAR CAMIÓN"):
                if pa:
                    run_query("INSERT INTO vehiculos VALUES (?,?,?)", (pa, "Unidad", suc_veh), commit=True)
                    st.rerun()
            
            q_veh = "SELECT * FROM vehiculos"
            if st.session_state.suc_ver != "Todas":
                q_veh += f" WHERE sucursal='{st.session_state.suc_ver}'"
            df_v = pd.DataFrame(run_query(q_veh))
            if not df_v.empty: df_v.columns = ["patente", "modelo", "sucursal"]
            st.table(df_v)

        with tabs[8]: # USUARIOS
            st.header("👥 Gestión de Usuarios")
            with st.form("new_user"):
                c1, c2, c3, c4 = st.columns(4)
                un = c1.text_input("Nuevo Usuario")
                pn = c2.text_input("Clave", type="password")
                rol = c3.selectbox("Rol", ["Operador", "Administrador"])
                suc_user = c4.selectbox("Sucursal", ["Sucursal A", "Sucursal B", "Todas"])
                if st.form_submit_button("CREAR USUARIO"):
                    if un and pn:
                        run_query("INSERT INTO usuarios VALUES (?,?,?,?)", (un, pn, rol, suc_user), commit=True)
                        st.success(f"Usuario {un} creado."); time.sleep(1); st.rerun()
            
            st.subheader("Usuarios Existentes")
            df_users = pd.DataFrame(run_query("SELECT user, rol, sucursal FROM usuarios"))
            if not df_users.empty: df_users.columns = ["user", "rol", "sucursal"]
            st.dataframe(df_users, use_container_width=True)
            
            u_del = st.selectbox("Seleccionar usuario para eliminar", [""] + df_users['user'].tolist() if not df_users.empty else [""])
            if st.button("🗑️ ELIMINAR USUARIO"):
                if u_del and u_del != "admin":
                    run_query("DELETE FROM usuarios WHERE user=?", (u_del,), commit=True)
                    st.success(f"Usuario {u_del} eliminado."); time.sleep(1); st.rerun()
