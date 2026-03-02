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

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import re

# ─────────────────────────────────────────────
# CONFIGURACIÓN ESTÉTICA
# ─────────────────────────────────────────────
st.set_page_config(page_title="Servicios de Logística", layout="wide")
st.markdown("""
    <style>
    .stApp { background-color: #F5F5DC; }
    h1, h2, h3, h4, h5, h6, p, li, td, th, span, label { color: #000000 !important; }
    div.stButton > button {
        background-color: #8DB600; color: black; font-weight: bold; border: 2px solid black;
    }
    /* Fix selectbox dropdown text color */
    [data-baseweb="select"] * { color: #000000 !important; }
    [data-baseweb="popover"] * { color: #000000 !important; background-color: #ffffff !important; }
    [data-baseweb="menu"] li { color: #000000 !important; }
    </style>
""", unsafe_allow_html=True)

IS_CLOUD = 'STREAMLIT_RUNTIME_ENV' in os.environ
DB_PATH  = 'gestion_banos.db'

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def extract_coords(url: str):
    """
    Extrae lat, lon de una URL de Google Maps.
    Soporta URLs largas, cortas (maps.app.goo.gl, goo.gl/maps) y todos los formatos comunes.
    Sigue automáticamente los redirects de URLs acortadas.
    """
    if not url:
        return None, None

    url = url.strip()

    def _parse_coords(u):
        """Intenta extraer coordenadas de una URL con varios patrones."""
        # Patrón 1: @lat,lon (URL de escritorio/web)
        m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Patrón 2: !3dlat!4dlon (embed/place)
        m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Patrón 3: q=lat,lon o q=lat%2Clon
        m = re.search(r'[?&]q=(-?\d+\.\d+)[,%2C]+(-?\d+\.\d+)', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Patrón 4: ll=lat,lon
        m = re.search(r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Patrón 5: /place/.../lat,lon,zoom
        m = re.search(r'/(-?\d+\.\d+),(-?\d+\.\d+),\d+z', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        # Patrón 6: center=lat,lon
        m = re.search(r'center=(-?\d+\.\d+),(-?\d+\.\d+)', u)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None

    # Primero intentar parsear la URL tal cual
    lat, lon = _parse_coords(url)
    if lat is not None:
        return lat, lon

    # Si es una URL corta (maps.app.goo.gl, goo.gl/maps, etc.), seguir el redirect
    is_short_url = any(d in url for d in ['maps.app.goo.gl', 'goo.gl/maps', 'g.co/maps'])
    if is_short_url or len(url) < 80:
        try:
            import requests as req_lib
            resp = req_lib.get(url, allow_redirects=True, timeout=5,
                               headers={"User-Agent": "Mozilla/5.0"})
            final_url = resp.url
            lat, lon = _parse_coords(final_url)
            if lat is not None:
                return lat, lon
        except Exception:
            pass

    return None, None

def diff_meses(f1, f2):
    """Calcula diferencia de meses entre dos fechas string d/m/y."""
    try:
        d1 = datetime.strptime(f1.split()[0], "%d/%m/%Y")
        d2 = datetime.strptime(f2.split()[0], "%d/%m/%Y")
        return (d1.year - d2.year) * 12 + d1.month - d2.month
    except:
        return 0

# ─────────────────────────────────────────────
# CAPA DE BASE DE DATOS — CONNECTION POOLING
# ─────────────────────────────────────────────

@st.cache_resource
def get_connection_pool():
    """
    Crea un pool de conexiones reutilizables (no abre/cierra por cada query).
    Se inicializa UNA sola vez durante la vida de la app.
    """
    return pool.SimpleConnectionPool(
        minconn=1,
        maxconn=5,
        host="aws-0-us-west-2.pooler.supabase.com",
        database="postgres",
        user="postgres.veswcqamiqyugxwtsrng",
        password="@Lex2110valentino",
        port=6543
    )

def run_query(query, params=(), commit=False):
    """Ejecuta una query usando el pool de conexiones (sin overhead de apertura)."""
    # Convertir '?' (SQLite style) a '%s' (psycopg2 style)
    query = query.replace('?', '%s')

    # Manejar el nombre reservado "user" en Postgres
    query = query.replace('usuarios WHERE user=', 'usuarios WHERE "user"=')
    query = query.replace('INSERT INTO usuarios VALUES', 'INSERT INTO usuarios ("user", password, rol, sucursal) VALUES')
    query = query.replace('DELETE FROM usuarios WHERE user=', 'DELETE FROM usuarios WHERE "user"=')
    query = query.replace('SELECT user, rol, sucursal', 'SELECT "user", rol, sucursal')
    query = query.replace('FROM usuarios WHERE user=', 'FROM usuarios WHERE "user"=')

    db_pool = get_connection_pool()
    conn = None
    try:
        conn = db_pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params if params else None)

        result = None
        if not commit:
            result = cur.fetchall()

        conn.commit()
        return result if result is not None else []
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        st.error(f"❌ Error en Base de Datos: {e}")
        return []
    finally:
        if conn:
            db_pool.putconn(conn)  # Devuelve al pool, NO la cierra

@st.cache_data(ttl=300)
def fetch_cached_table(table_name):
    """Obtiene una tabla completa con cache de Streamlit (5 min TTL)."""
    return pd.DataFrame(run_query(f"SELECT * FROM {table_name}"))

def load_all_data(force=False):
    """Carga todas las tablas principales en session_state para rapidez."""
    if 'data_loaded' not in st.session_state or force:
        if force:
            st.cache_data.clear()
        with st.spinner("Cargando datos..."):
            st.session_state.df_viajes   = fetch_cached_table("viajes")
            st.session_state.df_pagos    = fetch_cached_table("pagos")
            st.session_state.df_clientes = fetch_cached_table("clientes")
            st.session_state.df_stock    = fetch_cached_table("stock_playa")
            st.session_state.df_personal = fetch_cached_table("personal")
            st.session_state.df_gastos   = fetch_cached_table("gastos")
            st.session_state.df_vehiculos = fetch_cached_table("vehiculos")
            st.session_state.data_loaded = True

def refresh_table(table_name):
    """
    Actualización PARCIAL: refresca solo una tabla específica en session_state.
    Mucho más rápido que borrar todo el cache y recargar todo.
    """
    # Invalidar cache solo de esta tabla
    fetch_cached_table.clear()  # type: ignore
    df_nuevo = pd.DataFrame(run_query(f"SELECT * FROM {table_name}"))
    key_map = {
        "viajes":     "df_viajes",
        "pagos":      "df_pagos",
        "clientes":   "df_clientes",
        "stock_playa":"df_stock",
        "personal":   "df_personal",
        "gastos":     "df_gastos",
        "vehiculos":  "df_vehiculos",
    }
    if table_name in key_map:
        st.session_state[key_map[table_name]] = df_nuevo

def refresh_multiple(*tables):
    """Refresca múltiples tablas a la vez sin borrar todo el cache."""
    fetch_cached_table.clear()  # type: ignore
    key_map = {
        "viajes":     "df_viajes",
        "pagos":      "df_pagos",
        "clientes":   "df_clientes",
        "stock_playa":"df_stock",
        "personal":   "df_personal",
        "gastos":     "df_gastos",
        "vehiculos":  "df_vehiculos",
    }
    for table_name in tables:
        df_nuevo = pd.DataFrame(run_query(f"SELECT * FROM {table_name}"))
        if table_name in key_map:
            st.session_state[key_map[table_name]] = df_nuevo

def generar_html_impresion(titulo: str, subtitulo: str, df: pd.DataFrame, columnas: list, alias: dict = None) -> str:
    """
    Genera un HTML listo para imprimir (A4) con los datos del DataFrame.
    Se abre en una nueva pestaña del navegador y se dispara window.print().
    """
    alias = alias or {}
    fecha_hoy = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Encabezado de la tabla
    headers = "".join(f"<th>{alias.get(c, c)}</th>" for c in columnas if c in df.columns)

    # Filas
    rows_html = ""
    for _, row in df.iterrows():
        cells = "".join(f"<td>{row[c] if c in df.columns else ''}</td>" for c in columnas if c in df.columns)
        rows_html += f"<tr>{cells}</tr>"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>{titulo}</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 20px; color: #000; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; }}
  h2 {{ font-size: 13px; font-weight: normal; color: #555; margin-bottom: 16px; }}
  .fecha {{ font-size: 11px; color: #777; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ background-color: #2E7D32; color: white; padding: 7px 10px; text-align: left; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #ddd; }}
  tr:nth-child(even) {{ background-color: #f5f5f5; }}
  .deuda-alta {{ color: #c62828; font-weight: bold; }}
  @media print {{
    button {{ display: none; }}
    body {{ margin: 0; }}
  }}
</style>
</head>
<body>
<h1>🚚 SERVICIOS DE LOGÍSTICA — {titulo.upper()}</h1>
<h2>{subtitulo}</h2>
<div class="fecha">Generado: {fecha_hoy}</div>
<button onclick="window.print()" style="margin-bottom:16px;padding:8px 20px;background:#2E7D32;color:white;border:none;border-radius:4px;cursor:pointer;font-size:14px;">🖨️ Imprimir</button>
<table>
  <thead><tr>{headers}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p style="font-size:10px;color:#999;margin-top:20px;">Documento generado automáticamente — Servicios de Logística</p>
<script>
  // Esperar un momento para que cargue el HTML antes de imprimir
  // window.onload = () => setTimeout(() => window.print(), 500);
</script>
</body>
</html>"""
    return html


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
                    'SELECT rol, sucursal FROM usuarios WHERE "user"=%s AND password=%s',
                    (u, hash_password(p))
                )
                if res:
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
        st.write(f"**Base de Datos:** Supabase (PostgreSQL)")
        try:
            db_pool = get_connection_pool()
            conn = db_pool.getconn()
            db_pool.putconn(conn)
            st.success("✅ Conexión con Supabase establecida correctamente.")
        except Exception as e:
            st.error(f"❌ Error al conectar con Supabase: {e}")

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

        st.divider()
        if st.button("🔄 REFRESCAR DATOS", use_container_width=True):
            load_all_data(force=True)
            st.success("Datos actualizados.")
            time.sleep(0.5)
            st.rerun()

    # Cargar datos si no están en session_state
    load_all_data()

    # ─────────────────────────────────────────────
    # MIGRACIÓN: columna zona_servicio en clientes
    # ─────────────────────────────────────────────
    if 'zona_servicio_migrated' not in st.session_state:
        try:
            run_query("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS zona_servicio TEXT", commit=True)
        except Exception:
            pass
        st.session_state.zona_servicio_migrated = True

    # ─────────────────────────────────────────────
    # ENCABEZADO Y TABS
    # ─────────────────────────────────────────────
    st.markdown("<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: center; color: #8DB600 !important;'>📍 Gestionando: {st.session_state.suc_ver}</h3>", unsafe_allow_html=True)

    titulos = [
        "📋 CARGAS", "🗺️ MAPA", "📊 HISTORIAL",
        "👷 PERSONAL", "⛽ GASTOS", "💰 BALANCE", "👥 CLIENTES", "📖 MANUAL", "🛎️ SERVICIO"
    ]
    if st.session_state.rol == "Administrador":
        titulos += ["📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]

    tabs = st.tabs(titulos)

    # ─────────────────────────────────────────────
    # PESTAÑA 0: CARGAS
    # ─────────────────────────────────────────────
    with tabs[0]:
        st.header("Registro de Movimiento")

        # ✅ OPTIMIZADO: usar datos del session_state en lugar de queries directas
        df_vehiculos_cached = st.session_state.get('df_vehiculos', pd.DataFrame())
        df_stock_cached     = st.session_state.get('df_stock', pd.DataFrame())
        df_clientes_cached  = st.session_state.get('df_clientes', pd.DataFrame())

        if not df_vehiculos_cached.empty:
            if st.session_state.suc_ver != "Todas":
                v_list = df_vehiculos_cached[df_vehiculos_cached['sucursal'] == st.session_state.suc_ver]['patente'].tolist()
            else:
                v_list = df_vehiculos_cached['patente'].tolist()
        else:
            v_list = []

        mov        = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_buscar = "En Playa" if mov == "Entregado" else "En Calle"

        # ✅ OPTIMIZADO: filtrar desde el DataFrame en memoria
        if not df_stock_cached.empty:
            mask_est = df_stock_cached['estado'] == est_buscar
            if st.session_state.suc_ver != "Todas":
                mask_suc = df_stock_cached['sucursal'] == st.session_state.suc_ver
                u_dispo = df_stock_cached[mask_est & mask_suc]['nro_unit'].tolist()
            else:
                u_dispo = df_stock_cached[mask_est]['nro_unit'].tolist()
        else:
            u_dispo = []

        if not u_dispo:
            accion_label = "En Playa para entregar" if mov == "Entregado" else "En Calle para retirar"
            st.warning(f"⚠️ No hay unidades disponibles ({accion_label}). Verificar STOCK o hacer clic en 🔄 REFRESCAR DATOS.")

        # ✅ OPTIMIZADO: clientes desde session_state, filtrados por sucursal
        if not df_clientes_cached.empty:
            if 'sucursal' in df_clientes_cached.columns and st.session_state.suc_ver != "Todas":
                mask = (
                    (df_clientes_cached['sucursal'] == st.session_state.suc_ver) |
                    (df_clientes_cached['sucursal'] == 'Todas') |
                    (df_clientes_cached['sucursal'].isnull())
                )
                df_cli_filtrado = df_clientes_cached[mask]
            else:
                df_cli_filtrado = df_clientes_cached
            cli_dict = {f"{r['nombre']} (ID: {r['id']})": r['nombre'] for _, r in df_cli_filtrado.iterrows()}
        else:
            cli_dict = {}
        cli_list = [""] + list(cli_dict.keys())


        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            with c1:
                f_cli_sel = st.selectbox("Seleccionar Cliente (Base de Datos)", cli_list)
                f_cli_new = st.text_input("O escribir Cliente / Obra manualmente")
                f_cli_id  = None
                if f_cli_sel:
                    match_id = re.search(r'\(ID: (\d+)\)', f_cli_sel)
                    if match_id: f_cli_id = int(match_id.group(1))

                f_cli    = cli_dict.get(f_cli_sel, f_cli_new)
                f_dest   = st.text_input("Dirección de Entrega (Calle, Altura, Barrio)")
                f_maps   = st.text_input("🔗 URL Google Maps (Opcional)")
                f_tipo_c = st.selectbox("Contrato", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km     = st.number_input("Km", min_value=0.0)

                # — Zona de servicio (solo si hay cliente seleccionado de la lista) —
                ZONAS_SERVICIO = [
                    "",
                    "Zona 1 — Lun/Mar/Mié/Jue/Vie",
                    "Zona 2A — Mar/Vie",
                    "Zona 2B — Lun/Jue",
                    "Zona 3 — Lun/Mié/Vie",
                    "Zona 4 — Solo Lunes",
                    "Zona 5 — Solo Martes",
                    "Zona 6 — Solo Miércoles",
                    "Zona 7 — Solo Jueves",
                    "Zona 8 — Solo Viernes",
                ]
                zona_actual = ""
                if f_cli_id and not df_clientes_cached.empty:
                    row_cli = df_clientes_cached[df_clientes_cached['id'] == f_cli_id]
                    if not row_cli.empty and 'zona_servicio' in row_cli.columns:
                        zona_actual = row_cli.iloc[0].get('zona_servicio') or ""
                zona_idx = ZONAS_SERVICIO.index(zona_actual) if zona_actual in ZONAS_SERVICIO else 0
                f_zona = st.selectbox(
                    "📦 Zona de Servicio del Cliente",
                    ZONAS_SERVICIO,
                    index=zona_idx,
                    help="Asigna o cambia la zona de servicio de este cliente"
                )
            with c2:
                f_units = st.multiselect("Nº Unidades", u_dispo)
                f_pat   = st.selectbox("Vehículo", v_list if v_list else ["Sin Patente"])
                f_prec  = st.number_input("Precio Unitario ($)", min_value=0.0)
                f_pago  = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])

            if st.form_submit_button("GUARDAR"):
                if not f_units:
                    st.error("⚠️ Debés seleccionar al menos una unidad del listado 'Nº Unidades'.")
                elif not f_cli and mov != "Retirado":
                    st.error("⚠️ Debés ingresar el nombre del cliente o seleccionarlo de la lista.")
                else:
                    # --- VALIDAR DEUDA PARA RETIRO (OPTIMIZADO: query única con IN) ---
                    if mov == "Retirado" and f_units:
                        # ✅ OPTIMIZADO: una sola query batch para todos los viajes
                        placeholders = ','.join(['%s'] * len(f_units))
                        # Buscar últimos viajes activos para todas las unidades a la vez
                        df_v_cached = st.session_state.get('df_viajes', pd.DataFrame())
                        df_p_cached = st.session_state.get('df_pagos', pd.DataFrame())
                        unidades_con_deuda = []

                        for unit in f_units:
                            # Buscar en memoria (sin query a la DB)
                            if not df_v_cached.empty:
                                mask = (df_v_cached['unidades'].str.contains(unit, na=False)) & \
                                       (df_v_cached['tipo_mov'] == 'Entregado')
                                v_unit = df_v_cached[mask].sort_values('id', ascending=False)
                                if not v_unit.empty:
                                    v_row   = v_unit.iloc[0]
                                    v_id    = v_row['id']
                                    v_fecha = v_row['fecha']
                                    v_tipo  = v_row['tipo_contrato']
                                    v_punit = float(v_row['precio_unit'] or 0)

                                    meses = 1
                                    if v_tipo == "Mensual (Obra)":
                                        meses = max(1, diff_meses(datetime.now().strftime("%d/%m/%Y"), str(v_fecha)) + 1)
                                    total_dv = meses * v_punit

                                    # Pagos desde memoria
                                    if not df_p_cached.empty:
                                        pagos_unit = df_p_cached[df_p_cached['viaje_id'] == v_id]['monto'].sum()
                                        total_pg = float(pagos_unit or 0)
                                    else:
                                        total_pg = 0.0

                                    if total_pg < total_dv:
                                        unidades_con_deuda.append(f"{unit} (Deuda: ${total_dv - total_pg:,.0f})")

                        if unidades_con_deuda:
                            st.error(f"⚠️ NO SE PUEDE RETIRAR. Existen deudas pendientes: {', '.join(unidades_con_deuda)}")
                            st.stop()

                    lat, lon = None, None
                    if f_maps:
                        lat, lon = extract_coords(f_maps)
                    if not lat and f_dest and mov == "Entregado":
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
                        """INSERT INTO viajes (fecha, cliente, cliente_id, patente, destino, tipo_mov,
                           unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total,
                           estado_pago, lat, lon, sucursal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (fecha_h, f_cli, f_cli_id, f_pat, f_dest, mov, str_u, len(f_units),
                         f_tipo_c, f_km, f_prec, total_v, f_pago, lat, lon, st.session_state.sucursal),
                        commit=True
                    )
                    # ✅ OPTIMIZADO: UPDATE único con IN en lugar de un UPDATE por unidad
                    if len(f_units) == 1:
                        run_query("UPDATE stock_playa SET estado=%s WHERE nro_unit=%s", (nuevo_e, f_units[0]), commit=True)
                    else:
                        placeholders = ','.join(['%s'] * len(f_units))
                        run_query(
                            f"UPDATE stock_playa SET estado=%s WHERE nro_unit IN ({placeholders})",
                            (nuevo_e, *f_units),
                            commit=True
                        )

                    # ✓ Actualizar zona de servicio si se seleccionó un cliente de la lista
                    if f_cli_id and f_zona:
                        run_query(
                            "UPDATE clientes SET zona_servicio=%s WHERE id=%s",
                            (f_zona, f_cli_id), commit=True
                        )

                    # ✅ OPTIMIZADO: refrescar solo tablas afectadas
                    refresh_multiple("viajes", "stock_playa", "clientes")
                    st.success("✅ Guardado correctamente.")
                    time.sleep(0.8)
                    st.rerun()

    # ─────────────────────────────────────────────
    # PESTAÑA 1: MAPA
    # ─────────────────────────────────────────────
    with tabs[1]:
        col_tit, col_btn = st.columns([4, 1])
        col_tit.header("📍 Ubicación de Unidades")
        if col_btn.button("🔄 Actualizar Mapa", use_container_width=True):
            refresh_multiple("viajes", "stock_playa")
            st.rerun()

        df_v = st.session_state.df_viajes.copy()
        df_s = st.session_state.df_stock.copy()

        # ✅ FIJO: incluir registros propios de la sucursal Y los marcados como 'Todas'
        if st.session_state.suc_ver != "Todas":
            mask_v = (df_v['sucursal'] == st.session_state.suc_ver) | (df_v['sucursal'] == 'Todas')
            mask_s = (df_s['sucursal'] == st.session_state.suc_ver) | (df_s['sucursal'] == 'Todas')
            df_v = df_v[mask_v]
            df_s = df_s[mask_s]

        # Solo viajes con coordenadas válidas y tipo Entregado
        df_mapa = df_v[
            df_v['lat'].notnull() &
            df_v['lon'].notnull() &
            (df_v['tipo_mov'] == 'Entregado')
        ].copy()

        unidades_en_calle = set(df_s[df_s['estado'] == 'En Calle']['nro_unit'])

        st.caption(f"Registros con coordenadas disponibles: **{len(df_mapa)}**  |  Unidades en calle: **{len(unidades_en_calle)}**")

        if not df_mapa.empty:
            # Centrar el mapa en el promedio de las coordenadas disponibles
            centro_lat = float(df_mapa['lat'].mean())
            centro_lon = float(df_mapa['lon'].mean())
            m = folium.Map(location=[centro_lat, centro_lon], zoom_start=12)

            for _, row in df_mapa.iterrows():
                units_viaje = [u.strip() for u in str(row['unidades']).split(',')]
                es_activo = any(u in unidades_en_calle for u in units_viaje)
                color = 'red' if es_activo else 'orange'

                try:
                    lat_f = float(row['lat'])
                    lon_f = float(row['lon'])

                    # Número(s) de unidad para mostrar en el pin
                    label_unidad = str(row['unidades'])  # ej: "12" o "12, 15, 18"

                    # Color del pin: rojo=en calle, naranja=ya retirada
                    bg_color  = '#e53935' if es_activo else '#F57C00'
                    brd_color = '#b71c1c' if es_activo else '#E65100'

                    # Icono personalizado con el número de unidad visible
                    div_html = f"""
                    <div style="
                        background-color: {bg_color};
                        border: 2px solid {brd_color};
                        border-radius: 8px;
                        padding: 3px 7px;
                        color: white;
                        font-weight: bold;
                        font-size: 12px;
                        white-space: nowrap;
                        box-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                        text-align: center;
                    ">
                        🚽 {label_unidad}
                    </div>"""

                    folium.Marker(
                        location=[lat_f, lon_f],
                        popup=folium.Popup(
                            f"<b>{row['cliente']}</b><br>"
                            f"<b>Unidad(es):</b> {row['unidades']}<br>"
                            f"<b>Fecha:</b> {row['fecha']}<br>"
                            f"<b>Destino:</b> {row['destino']}<br>"
                            f"<b>Estado:</b> {'🔴 En calle' if es_activo else '  Retirada'}",
                            max_width=280
                        ),
                        tooltip=f"{'🔴' if es_activo else '🟡'} Unidad {label_unidad} — {row['cliente']}",
                        icon=folium.DivIcon(
                            html=div_html,
                            icon_size=(len(label_unidad) * 10 + 50, 30),
                            icon_anchor=(0, 15)
                        )
                    ).add_to(m)
                except Exception:
                    pass

            st_folium(m, width="100%", height=520, returned_objects=[])
            st.info("🔴 **Rojo** = Unidad actualmente entregada (en calle)  |  🟠 **Naranja** = Unidad retirada — la ubicación queda registrada permanentemente en el mapa")
        else:
            st.warning("⚠️ No hay registros con ubicación GPS para mostrar en el mapa.")
            st.info("Para que una unidad aparezca, pegá el **Link de Google Maps** al registrar la 'Entrega' en la pestaña 📋 CARGAS.")

    # ─────────────────────────────────────────────
    # PESTAÑA 2: HISTORIAL
    # ─────────────────────────────────────────────
    with tabs[2]:
        st.header("Historial")
        df_h = st.session_state.get('df_viajes', pd.DataFrame()).sort_values(by='id', ascending=False)
        st.dataframe(df_h, use_container_width=True)
        st.write("---")
        if not df_h.empty:
            id_sel = st.selectbox("Ver Remito / Editar ID", [""] + df_h['id'].astype(str).tolist())
            if id_sel:
                viaje_sel = df_h[df_h['id'] == int(id_sel)].iloc[0]
                with st.expander("📄 VER REMITO DIGITAL", expanded=True):
                    gps_url = f"https://www.google.com/maps?q={viaje_sel['lat']},{viaje_sel['lon']}" if viaje_sel['lat'] else "No disponible"

                    remito_txt = f"""
CONFORMIDAD: Responda este mensaje con un OK y su Nombre para confirmar recepción.

Lugar: {viaje_sel['sucursal']}
Fecha: {viaje_sel['fecha']}
Cliente: {viaje_sel['cliente']}
Producto: {viaje_sel['tipo_mov']} de Unidades
Cantidad: {viaje_sel['cantidad']}
Dirección: {viaje_sel['destino']}
Nº Unidad: {viaje_sel['unidades']}
GPS: {gps_url}

TÉRMINOS Y CONDICIONES:
* Las unidades no poseen seguro, por lo tanto, correrá por cuenta del cliente cualquier daño que sufran.
* Es responsabilidad del prestatario conservar las unidades en buen estado.
* Prohibido realizar traslados sin previo aviso.
* En caso de extravío o robo, el valor de reposición por unidad es de $450.000,00.
* La baja del alquiler será exclusivamente vía WhatsApp al telefono de contratacion.
* El sanitario no podrá estar a más de 10 mts. del estacionamiento del vehículo de desagote.
"""
                    st.code(remito_txt, language="text")

                with st.expander("✏️ Editar Registro"):
                    with st.form("form_edit"):
                        e_cli  = st.text_input("Cliente", value=viaje_sel['cliente'])
                        e_dest = st.text_input("Dirección (Calle, Altura, Barrio)", value=viaje_sel['destino'])
                        e_prec = st.number_input("Precio ($)", value=float(viaje_sel['precio_unit']))

                        # Mostrar coordenadas actuales como info
                        lat_actual = viaje_sel.get('lat')
                        lon_actual = viaje_sel.get('lon')
                        if lat_actual:
                            st.success(f"📍 Coordenadas actuales: {lat_actual:.6f}, {lon_actual:.6f}")
                        else:
                            st.warning("⚠️ Este registro no tiene ubicación en el mapa. Pegá la URL de Google Maps para agregarla.")

                        e_maps = st.text_input(
                            "🔗 URL Google Maps (pegá aquí para actualizar o agregar ubicación)",
                            placeholder="https://maps.app.goo.gl/... o URL larga de Google Maps"
                        )

                        if st.form_submit_button("APLICAR CAMBIOS"):
                            nuevo_total = viaje_sel['cantidad'] * e_prec

                            # Procesar URL Maps si se ingresó una
                            new_lat, new_lon = lat_actual, lon_actual
                            if e_maps.strip():
                                with st.spinner("Extrayendo coordenadas del mapa..."):
                                    ext_lat, ext_lon = extract_coords(e_maps.strip())
                                if ext_lat:
                                    new_lat, new_lon = ext_lat, ext_lon
                                    st.info(f"📍 Coordenadas extraídas: {new_lat:.6f}, {new_lon:.6f}")
                                else:
                                    st.warning("⚠️ No se pudieron extraer coordenadas de esa URL. Se mantienen las anteriores.")

                            run_query(
                                "UPDATE viajes SET cliente=%s, destino=%s, precio_unit=%s, total=%s, lat=%s, lon=%s WHERE id=%s",
                                (e_cli, e_dest, e_prec, nuevo_total, new_lat, new_lon, id_sel), commit=True
                            )
                            refresh_table("viajes")
                            st.success("✅ Registro actualizado correctamente.")
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
                    "INSERT INTO personal (fecha, nombre, tarea, pago, sucursal) VALUES (%s,%s,%s,%s,%s)",
                    (datetime.now().strftime("%d/%m/%Y"), p_nom, p_tar, p_mon, st.session_state.sucursal),
                    commit=True
                )
                # ✅ OPTIMIZADO: refrescar solo personal
                refresh_table("personal")
                st.rerun()

        df_p = st.session_state.get('df_personal', pd.DataFrame())
        if not df_p.empty:
            if st.session_state.suc_ver != "Todas":
                df_p = df_p[df_p['sucursal'] == st.session_state.suc_ver]
            st.dataframe(df_p, use_container_width=True)

            u_del_p = st.selectbox("Eliminar Pago Personal (ID)", [""] + df_p['id'].astype(str).tolist())
            if st.button("🗑️ Eliminar Pago"):
                if u_del_p:
                    run_query("DELETE FROM personal WHERE id=%s", (int(u_del_p),), commit=True)
                    refresh_table("personal")
                    st.rerun()
        else:
            st.info("No hay pagos de personal registrados.")

    # ─────────────────────────────────────────────
    # PESTAÑA 4: GASTOS
    # ─────────────────────────────────────────────
    with tabs[4]:
        st.header("⛽ Gastos")

        # ✅ OPTIMIZADO: usar vehiculos del session_state
        df_veh_cached = st.session_state.get('df_vehiculos', pd.DataFrame())
        if not df_veh_cached.empty:
            v_list_g = df_veh_cached[df_veh_cached['sucursal'] == st.session_state.sucursal]['patente'].tolist()
        else:
            v_list_g = []

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
                # ✅ OPTIMIZADO: refrescar solo gastos
                refresh_table("gastos")
                st.rerun()

        df_g = st.session_state.get('df_gastos', pd.DataFrame())
        if not df_g.empty:
            if st.session_state.suc_ver != "Todas":
                df_g = df_g[df_g['sucursal'] == st.session_state.suc_ver]
            st.dataframe(df_g, use_container_width=True)

            u_del_g = st.selectbox("Eliminar Gasto (ID)", [""] + df_g['id'].astype(str).tolist())
            if st.button("🗑️ Eliminar Gasto"):
                if u_del_g:
                    run_query("DELETE FROM gastos WHERE id=%s", (int(u_del_g),), commit=True)
                    refresh_table("gastos")
                    st.rerun()
        else:
            st.info("No hay gastos registrados.")

    # ─────────────────────────────────────────────
    # PESTAÑA 5: BALANCE
    # ─────────────────────────────────────────────
    with tabs[5]:
        st.header(f"💰 Balance de Caja - {st.session_state.suc_ver}")

        df_v = st.session_state.get('df_viajes', pd.DataFrame())
        df_p = st.session_state.get('df_pagos', pd.DataFrame())

        if st.session_state.suc_ver != "Todas":
            df_v = df_v[df_v['sucursal'] == st.session_state.suc_ver]
            df_p = df_p[df_p['sucursal'] == st.session_state.suc_ver]

        ingresos = float(df_p['monto'].sum()) if not df_p.empty else 0.0

        df_pers_cached = st.session_state.get('df_personal', pd.DataFrame())
        egresos_pers = 0.0
        if not df_pers_cached.empty and 'pago' in df_pers_cached.columns:
            if st.session_state.suc_ver != "Todas":
                df_pers_cached = df_pers_cached[df_pers_cached['sucursal'] == st.session_state.suc_ver]
            egresos_pers = pd.to_numeric(df_pers_cached['pago'], errors='coerce').sum()

        df_gast_cached = st.session_state.get('df_gastos', pd.DataFrame())
        egresos_gastos = 0.0
        if not df_gast_cached.empty and 'monto' in df_gast_cached.columns:
            if st.session_state.suc_ver != "Todas":
                df_gast_cached = df_gast_cached[df_gast_cached['sucursal'] == st.session_state.suc_ver]
            egresos_gastos = pd.to_numeric(df_gast_cached['monto'], errors='coerce').sum()

        neto = ingresos - (egresos_pers + egresos_gastos)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ingresos (Cobros)", f"${ingresos:,.2f}")
        col2.metric("Personal", f"-${egresos_pers:,.2f}")
        col3.metric("Gastos", f"-${egresos_gastos:,.2f}")
        col4.metric("CAJA NETO", f"${neto:,.2f}")

    # ─────────────────────────────────────────────
    # PESTAÑA 6: CLIENTES
    # ─────────────────────────────────────────────
    with tabs[6]:
        st.header("👥 Gestión de Clientes")
        with st.expander("➕ AGREGAR NUEVO CLIENTE"):
            with st.form("form_clientes"):
                c1, c2 = st.columns(2)
                c_nom = c1.text_input("Nombre / Razón Social")
                c_tel = c2.text_input("Teléfono")
                c_ema = c1.text_input("Email")
                c_cui = c2.text_input("CUIT")
                c_dni = c1.text_input("DNI")
                c_dir = c2.text_input("Dirección")
                if st.form_submit_button("GUARDAR CLIENTE"):
                    if c_nom:
                        # Guardar con la sucursal del operador
                        suc_cliente = st.session_state.sucursal
                        run_query(
                            "INSERT INTO clientes (nombre, telefono, email, cuit, dni, direccion, sucursal) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (c_nom, c_tel, c_ema, c_cui, c_dni, c_dir, suc_cliente), commit=True
                        )
                        refresh_table("clientes")
                        st.success(f"✅ Cliente {c_nom} guardado en {suc_cliente}.")
                        st.rerun()

        st.subheader("Listado de Clientes")
        df_clients_all = st.session_state.get('df_clientes', pd.DataFrame())

        # ✅ FILTRAR clientes por sucursal del usuario
        if not df_clients_all.empty and 'sucursal' in df_clients_all.columns:
            if st.session_state.suc_ver == "Todas":
                df_clients = df_clients_all.copy()
            else:
                # Ver solo clientes de esta sucursal O los marcados como 'Todas'
                mask_cli = (
                    (df_clients_all['sucursal'] == st.session_state.suc_ver) |
                    (df_clients_all['sucursal'] == 'Todas') |
                    (df_clients_all['sucursal'].isnull())
                )
                df_clients = df_clients_all[mask_cli].copy()
        else:
            df_clients = df_clients_all.copy()

        if not df_clients.empty:
            df_v = st.session_state.get('df_viajes', pd.DataFrame())
            df_p = st.session_state.get('df_pagos', pd.DataFrame())

            # --- CÁLCULO DE DEUDA DINÁMICA (EN MEMORIA) ---
            deudas = []
            for _, c_row in df_clients.iterrows():
                c_vjs = df_v[df_v['cliente_id'] == c_row['id']]
                total_deuda = 0
                for _, v in c_vjs.iterrows():
                    if v['tipo_mov'] == "Entregado":
                        meses = 1
                        if v['tipo_contrato'] == "Mensual (Obra)":
                            meses = max(1, diff_meses(datetime.now().strftime("%d/%m/%Y %H:%M"), v['fecha']) + 1)
                        dv = meses * (v['precio_unit'] if v['precio_unit'] else 0)
                        pagado = df_p[df_p['viaje_id'] == v['id']]['monto'].sum() if not df_p.empty else 0
                        total_deuda += max(0.0, float(dv) - float(pagado))
                deudas.append(total_deuda)

            df_clients = df_clients.copy()
            df_clients['Deuda Total ($)'] = deudas

            cols_show = [c for c in ['id', 'nombre', 'telefono', 'email', 'cuit', 'dni', 'direccion', 'sucursal', 'Deuda Total ($)'] if c in df_clients.columns or c == 'Deuda Total ($)']
            st.dataframe(df_clients[cols_show], use_container_width=True)

            # ⚡ BOTÓN IMPRIMIR CLIENTES CON DEUDA
            df_con_deuda = df_clients[df_clients['Deuda Total ($)'] > 0].sort_values('Deuda Total ($)', ascending=False).copy()
            df_con_deuda['Deuda Total ($)'] = df_con_deuda['Deuda Total ($)'].apply(lambda x: f'${x:,.0f}')
            if not df_con_deuda.empty:
                cols_impr = [c for c in ['nombre', 'telefono', 'direccion', 'sucursal', 'Deuda Total ($)'] if c in df_con_deuda.columns or c == 'Deuda Total ($)']
                alias_cli = {'nombre': 'Cliente', 'telefono': 'Teléfono', 'direccion': 'Dirección', 'sucursal': 'Sucursal', 'Deuda Total ($)': 'Deuda Total ($)'}
                html_deuda = generar_html_impresion(
                    titulo="Clientes con Deuda Pendiente",
                    subtitulo=f"Sucursal: {st.session_state.suc_ver} | Total clientes con deuda: {len(df_con_deuda)}",
                    df=df_con_deuda,
                    columnas=cols_impr,
                    alias=alias_cli
                )
                st.download_button(
                    label="🖨️ Imprimir / Descargar Lista de Deudas",
                    data=html_deuda.encode('utf-8'),
                    file_name=f"clientes_deuda_{datetime.now().strftime('%d%m%Y')}.html",
                    mime="text/html",
                    use_container_width=True
                )
            else:
                st.success("✅ No hay clientes con deuda pendiente.")

            st.divider()
            c_sel, c_pay = st.columns([1, 1])
            with c_sel:
                st.subheader("Registrar Pago")
                cid_input = st.selectbox("Seleccionar Cliente para ver Viajes", [""] + [f"{r['nombre']} (ID: {r['id']})" for _, r in df_clients.iterrows()])

            if cid_input:
                cid_val = int(re.search(r'ID: (\d+)', cid_input).group(1))
                v_activos = df_v[(df_v['cliente_id'] == cid_val) & (df_v['tipo_mov'] == 'Entregado')]
                if not v_activos.empty:
                    v_dict = {f"Viaje {r['id']} - {r['unidades']} ({r['fecha']})": r['id'] for _, r in v_activos.iterrows()}
                    v_sel = st.selectbox("Seleccionar Viaje/Alquiler", list(v_dict.keys()))
                    with c_pay:
                        st.subheader("Monto")
                        monto_p = st.number_input("Monto a Pagar ($)", min_value=0.0)
                        if st.button("CONFIRMAR PAGO"):
                            vid = v_dict[v_sel]
                            run_query("INSERT INTO pagos (viaje_id, monto, fecha, sucursal) VALUES (%s,%s,%s,%s)",
                                      (vid, monto_p, datetime.now().strftime("%d/%m/%Y"), st.session_state.sucursal), commit=True)
                            # ✅ OPTIMIZADO: refrescar solo pagos
                            refresh_table("pagos")
                            st.success("✅ Pago registrado correctamente.")
                            time.sleep(0.8)
                            st.rerun()
                else:
                    st.info("Este cliente no tiene alquileres activos para pagar.")

            st.divider()
            u_del_cli = st.selectbox("Eliminar Cliente (ID)", [""] + df_clients['id'].astype(str).tolist(), key="del_cli")
            if st.button("🗑️ Eliminar Cliente"):
                if u_del_cli:
                    run_query("DELETE FROM clientes WHERE id=%s", (int(u_del_cli),), commit=True)
                    refresh_table("clientes")
                    st.rerun()
        else:
            st.info("No hay clientes registrados.")

    # ─────────────────────────────────────────────
    # PESTAÑA 7: MANUAL DE USO — visible para todos
    # ─────────────────────────────────────────────
    with tabs[7]:
        st.header("📖 Manual de Uso del Sistema")
        st.markdown("""
## 🔑 Cómo ingresar
1. Ingresá tu **usuario** y **contraseña**
2. Hacé clic en **INGRESAR**

> 💡 Cada operador tiene su sucursal asignada. El Administrador ve todas las sucursales.

---

## 🗲️ Pestañas del sistema

| Pestaña | Para qué sirve |
|---|---|
| 📋 CARGAS | Registrar entregas y retiros de unidades |
| 🗺️ MAPA | Ver dónde están las unidades en este momento |
| 📊 HISTORIAL | Ver todos los movimientos anteriores |
| 👷 PERSONAL | Registrar pagos al personal |
| ⛽ GASTOS | Registrar gastos del vehículo |
| 💰 BALANCE | Resumen de caja (ingresos vs egresos) |
| 👥 CLIENTES | Gestionar clientes y registrar pagos |
| 📦 STOCK *(Admin)* | Ver y agregar unidades |
| 🚛 VEHÍCULOS *(Admin)* | Gestionar camiones |
| 👥 USUARIOS *(Admin)* | Crear y eliminar usuarios |

---

## 📋 Cómo registrar una ENTREGA

> Usa esto cuando **llevás un baño a un cliente**.

1. Ir a **📋 CARGAS**
2. Seleccionar **"Entregado"**
3. Completar el formulario:

| Campo | Qué poner |
|---|---|
| **Seleccionar Cliente** | Elgilo de la lista o escribilo manualmente |
| **Dirección de entrega** | Calle, número y barrio |
| **🔗 URL Google Maps** | Pegá el link de Maps *(para que aparezca en el mapa)* |
| **Contrato** | Mensual (Obra) / Eventual (Evento) |
| **Nº Unidades** | Seleccioná los baños a entregar |
| **Vehículo** | El camión que hace el traslado |
| **Precio Unitario** | El precio por baño |
| **Estado Pago** | Pagado / Pendiente |

4. Clic en **GUARDAR** ✅

> ⚠️ Si "Nº Unidades" aparece vacío: no hay baños disponibles. Verificar Stock.

---

## 📋 Cómo registrar un RETIRO

> Usa esto cuando **retirás un baño de donde estaba**.

1. Ir a **📋 CARGAS**
2. Seleccionar **"Retirado"**
3. Seleccionar las unidades a retirar
4. Clic en **GUARDAR**

> ⚠️ Si el cliente tiene **deuda pendiente**, el sistema NO permite retirar. Registrá el pago primero.

---

## 🗺️ Cómo usar el MAPA

- 🔴 **Rojo** = Unidad actualmente entregada (en calle)
- 🟠 **Naranja** = Unidad retirada — ubicación histórica permanente
- Hacé **click** sobre un marcador para ver: cliente, fecha y dirección
- Si no se actualizó: clic en **"🔄 Actualizar Mapa"**

---

## 👥 Cómo gestionar CLIENTES

**Agregar un cliente nuevo:**
1. Ir a **👥 CLIENTES** → abrir **"➕ AGREGAR NUEVO CLIENTE"**
2. Completar los datos y clic en **GUARDAR CLIENTE**

**Registrar un pago:**
1. Buscarlo en el selector "Seleccionar Cliente"
2. Elegir el alquiler correspondiente
3. Ingresar el monto y clic en **CONFIRMAR PAGO**

**Imprimir lista de deudas:**
- Botón **"🖨️ Imprimir / Descargar Lista de Deudas"** debajo de la tabla

---

## 📦 Stock e Impresión *(Admin)*

- El inventario muestra el estado de cada unidad: `En Playa` (disponible) o `En Calle` (alquilada)
- Botón **"🖨️ Imprimir Unidades Alquiladas"** → genera reporte con cliente y destino de cada unidad activa

---

## 💰 BALANCE

| Métrica | Qué muestra |
|---|---|
| Ingresos | Total de pagos recibidos |
| Personal | Total pagado al personal |
| Gastos | Combustible, repuestos, etc. |
| **CAJA NETO** | **Ingresos − Personal − Gastos** |

---

## 💡 Consejos útiles

| Situación | Qué hacer |
|---|---|
| "Nº Unidades" vacío | No hay baños en Playa. Verificar Stock o hacer un Retiro |
| El mapa no muestra la unidad | Agregar URL de Google Maps en la entrega o en Historial → Editar |
| No se puede retirar | El cliente tiene deuda. Registrar el pago primero |
| Datos desactualizados | Clic en **"🔄 REFRESCAR DATOS"** en el menú lateral |

---

## 🔐 Roles y permisos

| Rol | Puede hacer |
|---|---|
| **Operador** | Gestionar solo su sucursal |
| **Administrador** | Todo + Stock, Vehículos, Usuarios y ver todas las sucursales |
""")

    # ─────────────────────────────────────────────
    # PESTAÑA 8: SERVICIO
    # ─────────────────────────────────────────────
    with tabs[8]:
        st.header("🛎️ Planilla de Servicio")

        # Mapa de zonas → días en que corresponde servicio
        ZONA_DIAS = {
            "Zona 1 — Lun/Mar/Mié/Jue/Vie": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"],
            "Zona 2A — Mar/Vie":             ["Martes", "Viernes"],
            "Zona 2B — Lun/Jue":             ["Lunes", "Jueves"],
            "Zona 3 — Lun/Mié/Vie":          ["Lunes", "Miércoles", "Viernes"],
            "Zona 4 — Solo Lunes":           ["Lunes"],
            "Zona 5 — Solo Martes":          ["Martes"],
            "Zona 6 — Solo Miércoles":       ["Miércoles"],
            "Zona 7 — Solo Jueves":          ["Jueves"],
            "Zona 8 — Solo Viernes":         ["Viernes"],
        }
        NOMBRES_ZONAS = list(ZONA_DIAS.keys())

        # Modo de búsqueda: por día o por zona
        modo_serv = st.radio(
            "Ver servicio por:",
            ["📅 Día de la semana", "📦 Zona de servicio"],
            horizontal=True,
            key="serv_modo"
        )

        col_s1, col_s2 = st.columns([1, 2])
        with col_s1:
            if modo_serv == "📅 Día de la semana":
                dia_sel = st.selectbox(
                    "📅 Seleccionar día",
                    ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"],
                    key="serv_dia"
                )
                zona_sel = None
                titulo_sel = f"día {dia_sel}"
            else:
                zona_sel = st.selectbox(
                    "📦 Seleccionar zona",
                    NOMBRES_ZONAS,
                    key="serv_zona"
                )
                dia_sel = None
                titulo_sel = zona_sel

        # Obtener clientes filtrados por sucursal
        df_cli_serv = st.session_state.get('df_clientes', pd.DataFrame()).copy()

        if not df_cli_serv.empty and 'zona_servicio' in df_cli_serv.columns:
            if st.session_state.suc_ver != "Todas" and 'sucursal' in df_cli_serv.columns:
                mask_suc = (
                    (df_cli_serv['sucursal'] == st.session_state.suc_ver) |
                    (df_cli_serv['sucursal'] == 'Todas') |
                    (df_cli_serv['sucursal'].isnull())
                )
                df_cli_serv = df_cli_serv[mask_suc]

            if dia_sel:  # Filtrar por día
                def tiene_servicio_dia(zona):
                    if not zona or pd.isna(zona):
                        return False
                    return dia_sel in ZONA_DIAS.get(str(zona), [])
                df_servicio = df_cli_serv[df_cli_serv['zona_servicio'].apply(tiene_servicio_dia)].copy()
            else:  # Filtrar por zona exacta
                df_servicio = df_cli_serv[df_cli_serv['zona_servicio'] == zona_sel].copy()
        else:
            df_servicio = pd.DataFrame()

        # ── Enriquecer con coordenadas (desde viajes) y ordenar por proximidad ──
        if not df_servicio.empty:
            df_viajes_coords = st.session_state.get('df_viajes', pd.DataFrame())
            lat_map, lon_map = {}, {}

            if not df_viajes_coords.empty and 'cliente_id' in df_viajes_coords.columns:
                # Para cada cliente, tomar lat/lon del último viaje con coordenadas
                df_con_coords = df_viajes_coords[
                    df_viajes_coords['lat'].notnull() &
                    df_viajes_coords['lon'].notnull() &
                    (df_viajes_coords['tipo_mov'] == 'Entregado')
                ].sort_values('id', ascending=False)

                for _, v in df_con_coords.iterrows():
                    cid = v.get('cliente_id')
                    if cid and cid not in lat_map:
                        try:
                            lat_map[cid] = float(v['lat'])
                            lon_map[cid] = float(v['lon'])
                        except Exception:
                            pass

            df_servicio['_lat'] = df_servicio['id'].map(lat_map)
            df_servicio['_lon'] = df_servicio['id'].map(lon_map)

            tiene_coords = df_servicio['_lat'].notnull().sum()
            ordenar_prox = False
            if tiene_coords >= 2:
                ordenar_prox = st.checkbox(
                    f"🗺️ Ordenar por proximidad geográfica ({tiene_coords} de {len(df_servicio)} clientes con GPS)",
                    value=False,
                    key="serv_prox"
                )

            if ordenar_prox:
                # Algoritmo vecino más cercano (Nearest Neighbor)
                import math
                def haversine(lat1, lon1, lat2, lon2):
                    R = 6371
                    dlat = math.radians(lat2 - lat1)
                    dlon = math.radians(lon2 - lon1)
                    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
                    return R * 2 * math.asin(math.sqrt(a))

                # Separar clientes con y sin coordenadas
                df_con_gps  = df_servicio[df_servicio['_lat'].notnull()].copy()
                df_sin_gps  = df_servicio[df_servicio['_lat'].isnull()].copy()

                if not df_con_gps.empty:
                    indices_restantes = list(df_con_gps.index)
                    orden = []
                    # Punto de inicio: centroide de todos los clientes con GPS
                    lat_ini = df_con_gps['_lat'].mean()
                    lon_ini = df_con_gps['_lon'].mean()
                    current_lat, current_lon = lat_ini, lon_ini

                    while indices_restantes:
                        distancias = [
                            haversine(current_lat, current_lon,
                                      df_con_gps.loc[i, '_lat'], df_con_gps.loc[i, '_lon'])
                            for i in indices_restantes
                        ]
                        idx_min = indices_restantes[distancias.index(min(distancias))]
                        orden.append(idx_min)
                        current_lat = df_con_gps.loc[idx_min, '_lat']
                        current_lon = df_con_gps.loc[idx_min, '_lon']
                        indices_restantes.remove(idx_min)

                    df_servicio = pd.concat([
                        df_con_gps.loc[orden],
                        df_sin_gps
                    ]).reset_index(drop=True)
                    st.success(f"✅ Ruta optimizada por proximidad. Clientes sin GPS van al final.")

        # — Vista previa —
        st.subheader(f"👥 Clientes con servicio: {titulo_sel}")
        if not df_servicio.empty:
            cols_preview = [c for c in ['nombre', 'direccion', 'telefono', 'zona_servicio'] if c in df_servicio.columns]
            alias_prev = {'nombre': 'Nombre', 'direccion': 'Dirección', 'telefono': 'Teléfono', 'zona_servicio': 'Zona'}
            df_show = df_servicio[cols_preview].copy()
            df_show.columns = [alias_prev.get(c, c) for c in cols_preview]
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            st.info(f"📋 Total: **{len(df_servicio)}** cliente(s) para {titulo_sel}")

            # — Generar HTML de impresión con Firma y Aclaración —
            fecha_hoy = datetime.now().strftime("%d/%m/%Y")
            filas_html = ""
            for i, (_, row) in enumerate(df_servicio.iterrows(), start=1):
                nombre    = row.get('nombre', '')
                direccion = row.get('direccion', '')
                telefono  = row.get('telefono', '')
                bg = "#ffffff" if i % 2 == 1 else "#f9f9f9"
                filas_html += f"""
                <tr style="background:{bg};">
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd; font-weight:bold;">{i}</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd;">{nombre}</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd;">{direccion}</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd;">{telefono}</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd; width:110px;"></td>
                    <td style="padding:6px 8px; border-bottom:1px solid #ddd; width:130px;"></td>
                </tr>"""

            html_servicio = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Planilla de Servicio — {titulo_sel}</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 20px; color: #000; }}
  h1 {{ font-size: 18px; margin-bottom: 2px; }}
  .sub {{ font-size: 13px; color: #555; margin-bottom: 4px; }}
  .fecha {{ font-size: 11px; color: #777; margin-bottom: 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  thead tr {{ background-color: #2E7D32; color: white; }}
  th {{ padding: 8px; text-align: left; }}
  td {{ vertical-align: middle; }}
  @media print {{
    button {{ display: none; }}
    body {{ margin: 10px; }}
  }}
</style>
</head>
<body>
<h1>🛎️ PLANILLA DE SERVICIO — {titulo_sel.upper()}</h1>
<div class="sub">Sucursal: {st.session_state.suc_ver}</div>
<div class="fecha">Fecha: {fecha_hoy} &nbsp;|&nbsp; Total clientes: {len(df_servicio)}</div>
<button onclick="window.print()" style="margin-bottom:14px;padding:7px 20px;background:#2E7D32;color:white;border:none;border-radius:4px;cursor:pointer;font-size:13px;">🖨️ Imprimir</button>
<table>
  <thead>
    <tr>
      <th style="width:35px;">#</th>
      <th>Nombre</th>
      <th>Dirección</th>
      <th>Teléfono</th>
      <th>Firma</th>
      <th>Aclaración</th>
    </tr>
  </thead>
  <tbody>
    {filas_html}
  </tbody>
</table>
<p style="font-size:10px;color:#999;margin-top:18px;">Documento generado automáticamente — Servicios de Logística</p>
</body>
</html>"""

            nombre_archivo = (dia_sel or zona_sel or "servicio").lower().replace(" ", "_").replace("/", "")
            st.download_button(
                label=f"🖨️ Imprimir / Descargar Planilla ({titulo_sel})",
                data=html_servicio.encode('utf-8'),
                file_name=f"servicio_{nombre_archivo}_{datetime.now().strftime('%d%m%Y')}.html",
                mime="text/html",
                use_container_width=True
            )
        else:
            st.info(f"ℹ️ No hay clientes con servicio asignado para **{titulo_sel}**. Asignáles una zona desde la pestaña 📋 CARGAS al registrar una entrega.")


    # ─────────────────────────────────────────────
    # PESTAÑAS ADMIN
    # ─────────────────────────────────────────────
    if st.session_state.rol == "Administrador":
        # STOCK
        with tabs[9]:
            st.header("📦 Stock")
            c_input, c_view = st.columns([1, 2])
            with c_input:
                st.subheader("Agregar Unidad")
                tipo_u    = st.selectbox("Tipo", ["Baño Químico", "Contenedor", "Oficina Móvil"])
                nu        = st.text_input("ID Unidad")
                mo        = st.text_input("Modelo/Marca")
                suc_stock = st.selectbox("Sucursal Asignada", ["Sucursal A", "Sucursal B"])
                if st.button("GUARDAR STOCK", use_container_width=True):
                    if nu:
                        run_query("INSERT INTO stock_playa VALUES (%s,%s,%s,%s,%s)", (nu, tipo_u, mo, 'En Playa', suc_stock), commit=True)
                        refresh_table("stock_playa")
                        st.rerun()
            with c_view:
                st.subheader(f"Inventario - {st.session_state.suc_ver}")
                # ✅ OPTIMIZADO: usar session_state en lugar de query directa
                df_s_admin = st.session_state.get('df_stock', pd.DataFrame())
                st.dataframe(df_s_admin, use_container_width=True)

                # ⚡ BOTÓN IMPRIMIR UNIDADES ALQUILADAS (En Calle)
                if not df_s_admin.empty:
                    df_alquiladas = df_s_admin[df_s_admin['estado'] == 'En Calle'].copy()
                    if not df_alquiladas.empty:
                        # Enriquecer con info del último viaje
                        df_viajes_act = st.session_state.get('df_viajes', pd.DataFrame())
                        info_extra = []
                        for _, unit_row in df_alquiladas.iterrows():
                            nro = unit_row['nro_unit']
                            viaje_u = df_viajes_act[
                                (df_viajes_act['unidades'].str.contains(str(nro), na=False)) &
                                (df_viajes_act['tipo_mov'] == 'Entregado')
                            ].sort_values('id', ascending=False)
                            if not viaje_u.empty:
                                v = viaje_u.iloc[0]
                                info_extra.append({
                                    'nro_unit': nro,
                                    'tipo': unit_row.get('tipo', ''),
                                    'sucursal': unit_row.get('sucursal', ''),
                                    'cliente': v['cliente'],
                                    'destino': v['destino'],
                                    'fecha_entrega': v['fecha'],
                                    'precio_unit': f"${float(v['precio_unit'] or 0):,.0f}",
                                })
                            else:
                                info_extra.append({
                                    'nro_unit': nro, 'tipo': unit_row.get('tipo', ''),
                                    'sucursal': unit_row.get('sucursal', ''),
                                    'cliente': '-', 'destino': '-',
                                    'fecha_entrega': '-', 'precio_unit': '-',
                                })
                        df_impr_stock = pd.DataFrame(info_extra)
                        alias_stk = {
                            'nro_unit': 'Nº Unidad', 'tipo': 'Tipo', 'sucursal': 'Sucursal',
                            'cliente': 'Cliente', 'destino': 'Destino',
                            'fecha_entrega': 'Fecha Entrega', 'precio_unit': 'Precio'
                        }
                        html_stock = generar_html_impresion(
                            titulo="Unidades Actualmente Alquiladas",
                            subtitulo=f"Total unidades en calle: {len(df_impr_stock)}",
                            df=df_impr_stock,
                            columnas=list(alias_stk.keys()),
                            alias=alias_stk
                        )
                        st.download_button(
                            label="🖨️ Imprimir / Descargar Lista de Unidades Alquiladas",
                            data=html_stock.encode('utf-8'),
                            file_name=f"unidades_alquiladas_{datetime.now().strftime('%d%m%Y')}.html",
                            mime="text/html",
                            use_container_width=True
                        )
                    else:
                        st.info("No hay unidades actualmente alquiladas (todas en playa).")

        # VEHÍCULOS
        with tabs[10]:
            st.header("🚛 Vehículos")
            pa = st.text_input("Patente Camión").upper()
            suc_veh = st.selectbox("Sucursal Vehículo", ["Sucursal A", "Sucursal B"])
            if st.button("CARGAR CAMIÓN"):
                if pa:
                    run_query("INSERT INTO vehiculos VALUES (%s,%s,%s)", (pa, "Unidad", suc_veh), commit=True)
                    refresh_table("vehiculos")
                    st.rerun()
            # ✅ OPTIMIZADO: usar session_state en lugar de query directa
            df_v_admin = st.session_state.get('df_vehiculos', pd.DataFrame())
            st.table(df_v_admin)

        # USUARIOS
        with tabs[11]:
            st.header("👥 Usuarios")
            with st.form("new_user"):
                c1, c2, c3, c4 = st.columns(4)
                un = c1.text_input("Usuario")
                pn = c2.text_input("Clave", type="password")
                rol_new = c3.selectbox("Rol", ["Operador", "Administrador"])
                suc_user = c4.selectbox("Sucursal", ["Sucursal A", "Sucursal B", "Todas"])
                if st.form_submit_button("CREAR USUARIO"):
                    if un and pn:
                        run_query('INSERT INTO usuarios ("user", password, rol, sucursal) VALUES (%s,%s,%s,%s)', (un, hash_password(pn), rol_new, suc_user), commit=True)
                        load_all_data(force=True)
                        st.rerun()
            df_u = pd.DataFrame(run_query('SELECT "user", rol, sucursal FROM usuarios'))
            st.dataframe(df_u, use_container_width=True)

            st.divider()
            u_del = st.selectbox("Seleccionar Usuario para Eliminar", [""] + (df_u['user'].tolist() if not df_u.empty else []))
            if st.button("🗑️ ELIMINAR USUARIO"):
                if u_del:
                    if u_del == st.session_state.user:
                        st.error("No puedes eliminarte a ti mismo.")
                    else:
                        run_query('DELETE FROM usuarios WHERE "user"=%s', (u_del,), commit=True)
                        st.success(f"Usuario {u_del} eliminado.")
                        time.sleep(1)
                        st.rerun()
