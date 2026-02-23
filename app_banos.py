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
import re

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

def extract_coords(url: str):
    """Extrae lat, lon de una URL de Google Maps (soporta varios formatos)."""
    if not url: return None, None
    try:
        # Formato: ...@(-?\d+\.\d+),(-?\d+\.\d+)...
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        
        # Formato: ...!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)...
        match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
            
        # Formato query: q=(-?\d+\.\d+),(-?\d+\.\d+)
        match = re.search(r'q=(-?\d+\.\d+)%2C(-?\d+\.\d+)', url)
        if not match:
            match = re.search(r'q=(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
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

# CAPA DE BASE DE DATOS (SUPABASE)
# ─────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(
        host="aws-0-us-west-2.pooler.supabase.com",
        database="postgres",
        user="postgres.veswcqamiqyugxwtsrng",
        password="@Lex2110valentino",
        port=6543
    )

def run_query(query, params=(), commit=False):
    """Ejecuta una query en Supabase de forma segura."""
    # Convertir '?' (SQLite style) a '%s' (psycopg2 style) para compatibilidad
    query = query.replace('?', '%s')
    
    # Manejar el nombre reservado "user" en Postgres
    query = query.replace('usuarios WHERE user=', 'usuarios WHERE "user"=')
    query = query.replace('INSERT INTO usuarios VALUES', 'INSERT INTO usuarios ("user", password, rol, sucursal) VALUES')
    query = query.replace('DELETE FROM usuarios WHERE user=', 'DELETE FROM usuarios WHERE "user"=')
    query = query.replace('SELECT user, rol, sucursal', 'SELECT "user", rol, sucursal')
    query = query.replace('FROM usuarios WHERE user=', 'FROM usuarios WHERE "user"=')
    
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        
        result = None
        if not commit:
            result = cur.fetchall()
        
        conn.commit()
        return result
    except Exception as e:
        if conn:
            conn.rollback()
        st.error(f"❌ Error en Base de Datos: {e}")
        return []
    finally:
        if conn:
            conn.close()

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
        st.write(f"**Base de Datos:** Supabase (PostgreSQL)")
        try:
            conn = get_connection()
            st.success("✅ Conexión con Supabase establecida correctamente.")
            conn.close()
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

    # ─────────────────────────────────────────────
    # ENCABEZADO Y TABS
    # ─────────────────────────────────────────────
    st.markdown("<h1 style='text-align: center;'>🚚 SERVICIOS DE LOGÍSTICA</h1>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: center; color: #8DB600 !important;'>📍 Gestionando: {st.session_state.suc_ver}</h3>", unsafe_allow_html=True)

    titulos = [
        "📋 CARGAS", "🗺️ MAPA", "📊 HISTORIAL", 
        "👷 PERSONAL", "⛽ GASTOS", "💰 BALANCE", "👥 CLIENTES"
    ]
    if st.session_state.rol == "Administrador":
        titulos += ["📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"]

    tabs = st.tabs(titulos)

    # ─────────────────────────────────────────────
    # PESTAÑA 0: CARGAS
    # ─────────────────────────────────────────────
    with tabs[0]:
        st.header("Registro de Movimiento")

        if st.session_state.suc_ver != "Todas":
            v_list = [r['patente'] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal=%s", (st.session_state.suc_ver,))]
        else:
            v_list = [r['patente'] for r in run_query("SELECT patente FROM vehiculos")]

        mov        = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        est_buscar = "En Playa" if mov == "Entregado" else "En Calle"

        if st.session_state.suc_ver == "Todas":
            u_dispo = [r['nro_unit'] for r in run_query("SELECT nro_unit FROM stock_playa WHERE estado=%s", (est_buscar,))]
        else:
            u_dispo = [r['nro_unit'] for r in run_query("SELECT nro_unit FROM stock_playa WHERE estado=%s AND sucursal=%s", (est_buscar, st.session_state.suc_ver))]

        # Vincular Clientes
        rows_cli = run_query("SELECT id, nombre FROM clientes ORDER BY nombre ASC")
        cli_dict = {f"{r['nombre']} (ID: {r['id']})": r['nombre'] for r in rows_cli} if rows_cli else {}
        cli_list = [""] + list(cli_dict.keys())

        with st.form("form_viajes"):
            c1, c2 = st.columns(2)
            with c1:
                f_cli_sel = st.selectbox("Seleccionar Cliente (Base de Datos)", cli_list)
                f_cli_new = st.text_input("O escribir Cliente / Obra manualmente")
                # Extraer ID si es de la base de datos
                f_cli_id  = None
                if f_cli_sel:
                    match_id = re.search(r'\(ID: (\d+)\)', f_cli_sel)
                    if match_id: f_cli_id = int(match_id.group(1))
                
                f_cli     = cli_dict.get(f_cli_sel, f_cli_new)
                
                f_dest    = st.text_input("Dirección de Entrega")
                f_maps    = st.text_input("🔗 URL Google Maps (Opcional)")
                f_tipo_c  = st.selectbox("Contrato", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km      = st.number_input("Km", min_value=0.0)
            with c2:
                f_units = st.multiselect("Nº Unidades", u_dispo)
                f_pat   = st.selectbox("Vehículo", v_list if v_list else ["Sin Patente"])
                f_prec  = st.number_input("Precio Unitario ($)", min_value=0.0)
                f_pago  = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])

            if st.form_submit_button("GUARDAR"):
                if f_units and (f_cli or mov == "Retirado"):
                    # --- VALIDAR DEUDA PARA RETIRO ---
                    if mov == "Retirado":
                        unidades_con_deuda = []
                        for unit in f_units:
                            # Buscar el último viaje de entrega ACTIVO para esta unidad
                            v_act = run_query(
                                "SELECT id, precio_unit, fecha, tipo_contrato FROM viajes WHERE unidades LIKE %s AND tipo_mov='Entregado' ORDER BY id DESC LIMIT 1",
                                (f"%{unit}%",)
                            )
                            if v_act:
                                v_id = v_act[0]['id']
                                v_fecha = v_act[0]['fecha']
                                v_tipo = v_act[0]['tipo_contrato']
                                v_punit = v_act[0]['precio_unit']
                                
                                # Calcular Total Devengado
                                meses = 1
                                if v_tipo == "Mensual (Obra)":
                                    meses = max(1, diff_meses(datetime.now().strftime("%d/%m/%Y"), v_fecha) + 1)
                                total_dv = meses * v_punit
                                
                                # Calcular Pagos Realizados
                                pagos_v = run_query("SELECT SUM(monto) as total FROM pagos WHERE viaje_id=%s", (v_id,))
                                total_pg = float(pagos_v[0]['total'] or 0)
                                
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
                    for unit in f_units:
                        run_query("UPDATE stock_playa SET estado=%s WHERE nro_unit=%s", (nuevo_e, unit), commit=True)

                    st.success("✅ Guardado correctamente.")
                    time.sleep(1)
                    st.rerun()

    # ─────────────────────────────────────────────
    # PESTAÑA 1: MAPA
    # ─────────────────────────────────────────────
    with tabs[1]:
        st.header("Ubicación de Unidades")
        if st.session_state.suc_ver != "Todas":
            rows_mapa = run_query("SELECT cliente, destino, unidades, lat, lon, tipo_mov FROM viajes WHERE lat IS NOT NULL AND sucursal=%s", (st.session_state.suc_ver,))
            rows_calle = run_query("SELECT nro_unit FROM stock_playa WHERE estado='En Calle' AND sucursal=%s", (st.session_state.suc_ver,))
        else:
            rows_mapa  = run_query("SELECT cliente, destino, unidades, lat, lon, tipo_mov FROM viajes WHERE lat IS NOT NULL")
            rows_calle = run_query("SELECT nro_unit FROM stock_playa WHERE estado='En Calle'")

        df_mapa = pd.DataFrame(rows_mapa)
        unidades_en_calle = set(r['nro_unit'] for r in rows_calle) if rows_calle else set()

        if not df_mapa.empty:
            m = folium.Map(location=[-31.4135, -64.1810], zoom_start=11)
            for _, row in df_mapa.iterrows():
                units_viaje = [u.strip() for u in str(row['unidades']).split(',')]
                es_activo   = (row['tipo_mov'] == 'Entregado') and any(u in unidades_en_calle for u in units_viaje)
                color = 'red' if es_activo else 'orange'
                if row['tipo_mov'] == 'Retirado': color = 'orange'
                folium.Marker(
                    [row['lat'], row['lon']],
                    popup=f"<b>{row['cliente']}</b><br>{row['unidades']}<br><i>{row['tipo_mov']}</i>",
                    icon=folium.Icon(color=color)
                ).add_to(m)
            st_folium(m, width=1100, height=500)
            st.info("🔴 Rojo: Activos | 🟠 Naranja: Historial/Retirados")
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
            id_sel = st.selectbox("Ver Remito / Editar ID", [""] + df_h['id'].astype(str).tolist())
            if id_sel:
                viaje_sel = df_h[df_h['id'] == int(id_sel)].iloc[0]
                with st.expander("📄 VER REMITO DIGITAL", expanded=True):
                    remito_txt = f"""
CONFORMIDAD: Responda este mensaje con un OK y su Nombre para confirmar recepción.

Lugar: {viaje_sel['sucursal']}
Fecha: {viaje_sel['fecha']}
Cliente: {viaje_sel['cliente']}
Producto: {viaje_sel['tipo_mov']} de Unidades
Cantidad: {viaje_sel['cantidad']}
Dirección: {viaje_sel['destino']}
Nº Unidad: {viaje_sel['unidades']}

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
                        e_cli  = st.text_input("Cliente",   value=viaje_sel['cliente'])
                        e_dest = st.text_input("Dirección", value=viaje_sel['destino'])
                        e_prec = st.number_input("Precio ($)", value=float(viaje_sel['precio_unit']))
                        if st.form_submit_button("APLICAR CAMBIOS"):
                            nuevo_total = viaje_sel['cantidad'] * e_prec
                            run_query(
                                "UPDATE viajes SET cliente=%s, destino=%s, precio_unit=%s, total=%s WHERE id=%s",
                                (e_cli, e_dest, e_prec, nuevo_total, id_sel), commit=True
                            )
                            st.success("Actualizado")
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
                st.rerun()
        rows_p = run_query("SELECT * FROM personal ORDER BY id DESC")
        df_p = pd.DataFrame(rows_p)
        st.dataframe(df_p, use_container_width=True)

    # ─────────────────────────────────────────────
    # PESTAÑA 4: GASTOS
    # ─────────────────────────────────────────────
    with tabs[4]:
        st.header("⛽ Gastos")
        v_list_g = [r['patente'] for r in run_query("SELECT patente FROM vehiculos WHERE sucursal=%s", (st.session_state.sucursal,))]
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
                if not res: return 0.0
                df_temp = pd.DataFrame(res)
                if cond_suc: df_temp = df_temp[df_temp["sucursal"] == cond_suc]
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
            st.info(f"Esperando datos... ({e})")

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
                        run_query(
                            "INSERT INTO clientes (nombre, telefono, email, cuit, dni, direccion) VALUES (%s,%s,%s,%s,%s,%s)",
                            (c_nom, c_tel, c_ema, c_cui, c_dni, c_dir), commit=True
                        )
                        st.success(f"✅ Cliente {c_nom} guardado.")
                        st.rerun()

        st.subheader("Listado de Clientes")
        rows_clients = run_query("SELECT * FROM clientes ORDER BY nombre ASC")
        if rows_clients:
            df_clients = pd.DataFrame(rows_clients)
            
            # --- CÁLCULO DE DEUDA DINÁMICA ---
            deudas = []
            for _, c_row in df_clients.iterrows():
                # Viajes de este cliente
                vjs = run_query("SELECT id, precio_unit, fecha, tipo_contrato, tipo_mov FROM viajes WHERE cliente_id=%s", (c_row['id'],))
                total_deuda = 0
                for v in vjs:
                    if v['tipo_mov'] == "Entregado":
                        # Calcular meses devengados
                        meses = 1
                        if v['tipo_contrato'] == "Mensual (Obra)":
                            meses = max(1, diff_meses(datetime.now().strftime("%d/%m/%Y %H:%M"), v['fecha']) + 1)
                        dv = meses * v['precio_unit']
                        
                        # Restar lo pagado
                        pgs = run_query("SELECT SUM(monto) as total FROM pagos WHERE viaje_id=%s", (v['id'],))
                        pagado = float(pgs[0]['total'] or 0)
                        total_deuda += max(0.0, dv - pagado)
                deudas.append(total_deuda)
            
            df_clients['Deuda Total ($)'] = deudas
            st.dataframe(df_clients, use_container_width=True)
            
            st.divider()
            c_sel, c_pay = st.columns([1, 1])
            with c_sel:
                st.subheader("Registrar Pago")
                cid_input = st.selectbox("Seleccionar Cliente para ver Viajes", [""] + [f"{r['nombre']} (ID: {r['id']})" for r in rows_clients])
            
            if cid_input:
                cid_val = int(re.search(r'ID: (\d+)', cid_input).group(1))
                v_activos = run_query("SELECT id, fecha, unidades, precio_unit, tipo_contrato FROM viajes WHERE cliente_id=%s AND tipo_mov='Entregado' ORDER BY id DESC", (cid_val,))
                if v_activos:
                    v_dict = {f"Viaje {v['id']} - {v['unidades']} ({v['fecha']})": v['id'] for v in v_activos}
                    v_sel = st.selectbox("Seleccionar Viaje/Alquiler", list(v_dict.keys()))
                    with c_pay:
                        st.subheader("Monto")
                        monto_p = st.number_input("Monto a Pagar ($)", min_value=0.0)
                        if st.button("CONFIRMAR PAGO"):
                            vid = v_dict[v_sel]
                            run_query("INSERT INTO pagos (viaje_id, monto, fecha, sucursal) VALUES (%s,%s,%s,%s)",
                                      (vid, monto_p, datetime.now().strftime("%d/%m/%Y"), st.session_state.sucursal), commit=True)
                            st.success("✅ Pago registrado correctamente.")
                            time.sleep(1)
                            st.rerun()
                else:
                    st.info("Este cliente no tiene alquileres activos para pagar.")

            st.divider()
            u_del_cli = st.selectbox("Eliminar Cliente (ID)", [""] + df_clients['id'].astype(str).tolist(), key="del_cli")
            if st.button("🗑️ Eliminar Cliente"):
                if u_del_cli:
                    run_query("DELETE FROM clientes WHERE id=%s", (int(u_del_cli),), commit=True)
                    st.rerun()

    # ─────────────────────────────────────────────
    # PESTAÑAS ADMIN
    # ─────────────────────────────────────────────
    if st.session_state.rol == "Administrador":
        # STOCK
        with tabs[7]:
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
                        st.rerun()
            with c_view:
                st.subheader(f"Inventario - {st.session_state.suc_ver}")
                rows_s = run_query("SELECT * FROM stock_playa")
                df_s = pd.DataFrame(rows_s)
                st.dataframe(df_s, use_container_width=True)

        # VEHÍCULOS
        with tabs[8]:
            st.header("🚛 Vehículos")
            pa = st.text_input("Patente Camión").upper()
            suc_veh = st.selectbox("Sucursal Vehículo", ["Sucursal A", "Sucursal B"])
            if st.button("CARGAR CAMIÓN"):
                if pa:
                    run_query("INSERT INTO vehiculos VALUES (%s,%s,%s)", (pa, "Unidad", suc_veh), commit=True)
                    st.rerun()
            df_v = pd.DataFrame(run_query("SELECT * FROM vehiculos"))
            st.table(df_v)

        # USUARIOS
        with tabs[9]:
            st.header("👥 Usuarios")
            with st.form("new_user"):
                c1, c2, c3, c4 = st.columns(4)
                un = c1.text_input("Usuario")
                pn = c2.text_input("Clave", type="password")
                rol_new = c3.selectbox("Rol", ["Operador", "Administrador"])
                suc_user = c4.selectbox("Sucursal", ["Sucursal A", "Sucursal B", "Todas"])
                if st.form_submit_button("CREAR USUARIO"):
                    if un and pn:
                        run_query("INSERT INTO usuarios VALUES (%s,%s,%s,%s)", (un, hash_password(pn), rol_new, suc_user), commit=True)
                        st.rerun()
            df_u = pd.DataFrame(run_query("SELECT \"user\", rol, sucursal FROM usuarios"))
            st.dataframe(df_u, use_container_width=True)
