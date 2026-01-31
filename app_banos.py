import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Latin Servicios", layout="wide")

# --- BASE DE DATOS ---
conn = sqlite3.connect('latin_servicios_v5.db', check_same_thread=False)
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
if 'login' not in st.session_state: st.session_state.login = False

if not st.session_state.login:
    st.markdown("<h1 style='text-align: center;'>🚚 LATIN SERVICIOS</h1>", unsafe_allow_html=True)
    u = st.text_input("Usuario")
    p = st.text_input("Clave", type="password")
    if st.button("INGRESAR"):
        c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
        res = c.fetchone()
        if res:
            st.session_state.login, st.session_state.rol = True, res[0]
            st.rerun()
else:
    tabs = st.tabs(["📋 CARGAS", "📊 HISTORIAL", "📦 STOCK/UNIDADES", "🚛 VEHÍCULOS", "👥 USUARIOS"])

    # 1. CARGAS (MULTIPLE Y TIPO CONTRATO)
    with tabs[0]:
        st.header("Registro de Movimiento")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        
        mov = st.radio("Acción", ["Entregado", "Retirado"], horizontal=True)
        estado_filtro = "En Playa" if mov == "Entregado" else "En Calle"
        
        # Obtenemos unidades disponibles según el movimiento
        u_dispo = pd.read_sql_query(f"SELECT nro_unit FROM stock_playa WHERE estado='{estado_filtro}'", conn)['nro_unit'].tolist()
        
        with st.form("form_viajes"):
            col1, col2 = st.columns(2)
            with col1:
                f_cli = st.text_input("Cliente / Nombre del Evento")
                f_dest = st.text_input("Dirección / Ubicación")
                f_tipo_c = st.selectbox("Tipo de Contratación", ["Mensual (Obra)", "Eventual (Evento)"])
                f_km = st.number_input("Km Recorridos", min_value=0.0)
            with col2:
                # AQUÍ SE PUEDEN ELEGIR MUCHAS UNIDADES
                f_units = st.multiselect("Seleccionar Unidades", u_dispo)
                f_pat = st.selectbox("Vehículo", v_list)
                f_prec = st.number_input("Precio por unidad ($)", min_value=0.0)
                f_pago = st.selectbox("Estado Pago", ["Pendiente", "Pagado"])
            
            if st.form_submit_button("GUARDAR MOVIMIENTO"):
                if f_units and v_list:
                    cant = len(f_units)
                    total_viaje = cant * f_prec
                    str_units = ", ".join(f_units)
                    nuevo_estado = "En Calle" if mov == "Entregado" else "En Playa"
                    
                    c.execute("""INSERT INTO viajes (fecha, cliente, patente, destino, tipo_mov, 
                              unidades, cantidad, tipo_contrato, km_entrega, precio_unit, total, estado_pago) 
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (datetime.now().strftime("%d/%m/%Y"), f_cli, f_pat, f_dest, mov, 
                               str_units, cant, f_tipo_c, f_km, f_prec, total_viaje, f_pago))
                    
                    # Actualizamos el estado de cada unidad seleccionada
                    for unit in f_units:
                        c.execute("UPDATE stock_playa SET estado = ? WHERE nro_unit = ?", (nuevo_estado, unit))
                    
                    conn.commit()
                    st.success(f"✅ Registrado: {cant} unidades como {nuevo_estado}. Total: ${total_viaje}")
                    st.rerun()

    # 2. HISTORIAL
    with tabs[1]:
        st.header("Historial y Reportes")
        df_h = pd.read_sql_query("SELECT * FROM viajes", conn)
        st.dataframe(df_h, use_container_width=True)
        if not df_h.empty:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_h.to_excel(writer, index=False)
            st.download_button("📥 DESCARGAR EXCEL", output.getvalue(), "Reporte_Latin.xlsx")

    # 3. STOCK
    with tabs[2]:
        st.header("Control de Unidades Físicas")
        c1, c2 = st.columns([1, 2])
        with c1:
            st.subheader("Alta de Baño")
            nu = st.text_input("Nº de Unidad (Ej: B-01)")
            mod = st.text_input("Modelo")
            if st.button("Guardar en Sistema"):
                c.execute("INSERT OR IGNORE INTO stock_playa VALUES (?,?,'En Playa')", (nu, mod))
                conn.commit()
                st.rerun()
        with c2:
            st.subheader("Estado de la Flota")
            st.table(pd.read_sql_query("SELECT * FROM stock_playa", conn))

    # 4 y 5 (Vehículos y Usuarios se mantienen igual)
    with tabs[3]:
        p = st.text_input("Nueva Patente").upper()
        if st.button("Cargar"):
            c.execute("INSERT OR IGNORE INTO vehiculos VALUES (?,?)", (p, "Unidad"))
            conn.commit(); st.rerun()
        st.table(pd.read_sql_query("SELECT * FROM vehiculos", conn))

    with tabs[4]:
        un = st.text_input("Usuario")
        pn = st.text_input("Clave", type="password")
        if st.button("Crear"):
            c.execute("INSERT OR IGNORE INTO usuarios VALUES (?,?,'Operador')", (un, pn))
            conn.commit(); st.rerun()
        st.table(pd.read_sql_query("SELECT user, rol FROM usuarios", conn))
