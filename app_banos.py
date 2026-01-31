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
conn = sqlite3.connect('latin_v10.db', check_same_thread=False)
c = conn.cursor()

c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, patente TEXT, 
              destino TEXT, tipo_mov TEXT, unidades TEXT, cantidad INTEGER, 
              tipo_contrato TEXT, km_entrega REAL, precio_unit REAL, total REAL, estado_pago TEXT)''')
c.execute('CREATE TABLE IF NOT EXISTS stock_playa (nro_unit TEXT PRIMARY KEY, modelo TEXT, color TEXT, estado TEXT)')

c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

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
        else: st.error("Acceso incorrecto")
else:
    st.sidebar.title("LATIN SERVICIOS")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.login = False
        st.rerun()

    tabs = st.tabs(["📋 CARGAS", "📊 HISTORIAL", "📦 STOCK", "🚛 VEHÍCULOS", "👥 USUARIOS"] if st.session_state.rol == "Administrador" else ["📋 CARGAS", "📊 HISTORIAL"])

    # --- PESTAÑA 1: CARGAS + REMITO LEGAL ---
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
            
            if st.form_submit_button("GUARDAR Y GENERAR REMITO DIGITAL"):
                if f_units and v_list:
                    fecha_h = datetime.now().strftime("%d/%m/%Y %H:%M")
                    str_u = ", ".join(f_units)
                    total_v = len(f_units) * f_prec
                    nuevo_e = "En Calle" if mov == "Entregado" else "En Playa"
                    
                    c.execute("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                              unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago) 
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (fecha_h, f_cli, f_pat, f_dest, mov, str_u, len(f_units), f_tipo_c, f_km, f_prec, total_v, f_pago))
                    
                    for unit in f_units:
                        c.execute("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_e, unit))
                    conn.commit()
                    
                    # ARMADO DEL REMITO PARA WHATSAPP
                    st.session_state.remito_ok = f"""*REMITO DIGITAL - LATIN SERVICIOS*
-------------------------------------------
*CONFORMIDAD:* Responda este mensaje con un *OK* y su Nombre para confirmar recepción.
-------------------------------------------
✅ *{mov.upper()}*
📅 Fecha: {fecha_h}
👤 Cliente: {f_cli}
📍 Destino: {f_dest}
📋 Contrato: {f_tipo_c}
🚽 Unidades: {str_u}
💰 Total: ${total_v:,.2f}

*TÉRMINOS Y CONDICIONES:*
• Las unidades no poseen seguro, por lo tanto, correrá por cuenta del cliente cualquier daño que sufran.
• Es responsabilidad del prestatario conservar las unidades en buen estado.
• Prohibido realizar traslados sin previo aviso.
• En caso de extravío o robo, el valor de reposición por unidad es de *$450.000,00*.
• La baja del alquiler será exclusivamente vía WhatsApp al *3513090069*.
• El sanitario no podrá estar a más de 12 mts. del estacionamiento del vehículo de desagote.
-------------------------------------------"""
                    st.success("✅ Datos guardados.")
                    st.rerun()

        if 'remito_ok' in st.session_state:
            st.info("📱 Copiá el texto para WhatsApp:")
            st.code(st.session_state.remito_ok)

    # --- PESTAÑA 2: HISTORIAL ---
    with tabs[1]:
        st.header("Historial")
        df_h = pd.read_sql_query("SELECT * FROM viajes", conn)
        st.dataframe(df_h, use_container_width=True)
        if not df_h.empty:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_h.to_excel(writer, index=False)
            st.download_button("📥 EXPORTAR EXCEL", output.getvalue(), "Reporte_Latin.xlsx")

    # --- RESTO DE PESTAÑAS (ADMIN) ---
    if st.session_state.rol == "Administrador":
        with tabs[2]: # STOCK
            st.subheader("Alta de Baños")
            nu = st.text_input("Nº Baño")
            mo = st.text_input("Modelo")
            if st.button("Guardar"):
                c.execute("INSERT OR IGNORE INTO stock_playa VALUES (?,?,?,'En Playa')", (nu, mo, ""))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM stock_playa", conn))

        with tabs[3]: # VEHICULOS
            st.subheader("Alta de Camiones")
            pa = st.text_input("Patente").upper()
            if st.button("Cargar"):
                c.execute("INSERT OR IGNORE INTO vehiculos VALUES (?,?)", (pa, "Unidad"))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

        with tabs[4]: # USUARIOS
            st.subheader("Gestión de Usuarios")
            un = st.text_input("Nombre")
            pn = st.text_input("Clave", type="password")
            if st.button("Crear"):
                c.execute("INSERT OR IGNORE INTO usuarios VALUES (?,?,'Operador')", (un, pn))
                conn.commit(); st.rerun()
            st.table(pd.read_sql_query("SELECT user, rol FROM usuarios", conn))
