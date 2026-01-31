import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

# --- CONEXIÓN ---
conn = sqlite3.connect('logistica_empresa.db', check_same_thread=False)
c = conn.cursor()

# Tablas
c.execute('CREATE TABLE IF NOT EXISTS usuarios (user TEXT PRIMARY KEY, password TEXT, rol TEXT)')
c.execute('CREATE TABLE IF NOT EXISTS vehiculos (patente TEXT PRIMARY KEY, modelo TEXT)')
c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, cliente TEXT, chofer TEXT, 
              patente TEXT, precio REAL, estado_pago TEXT, destino TEXT)''')

# Usuario semilla (Asegura que siempre haya un admin al principio)
c.execute("INSERT OR IGNORE INTO usuarios VALUES ('admin', 'admin123', 'Administrador')")
conn.commit()

# --- SESIÓN ---
if 'login' not in st.session_state:
    st.session_state['login'] = False
    st.session_state['user'] = ""
    st.session_state['rol'] = ""

if not st.session_state['login']:
    st.title("🔑 Sistema de Baños: Ingreso")
    u = st.text_input("Usuario")
    p = st.text_input("Contraseña", type="password")
    if st.button("Entrar"):
        c.execute("SELECT rol FROM usuarios WHERE user=? AND password=?", (u, p))
        res = c.fetchone()
        if res:
            st.session_state['login'] = True
            st.session_state['user'] = u
            st.session_state['rol'] = res[0]
            st.rerun()
        else:
            st.error("Usuario o clave incorrectos")
else:
    st.sidebar.title(f"Hola, {st.session_state['user']}")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state['login'] = False
        st.rerun()

    # Definimos las pestañas. Si es Admin, ve la de Usuarios.
    if st.session_state['rol'] == "Administrador":
        tabs = st.tabs(["📝 Carga", "📊 Historial/Edición", "🚛 Flota", "👥 Usuarios"])
    else:
        tabs = st.tabs(["📝 Carga", "📊 Historial"])

    # --- PESTAÑA 1: CARGA ---
    with tabs[0]:
        st.header("Registrar Servicio")
        v_list = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
        with st.form("carga"):
            f_cli = st.text_input("Cliente")
            f_dest = st.text_input("Destino")
            f_pre = st.number_input("Precio ($)", min_value=0.0)
            f_pat = st.selectbox("Vehículo", v_list if v_list else ["Sin vehículos"])
            if st.form_submit_button("Guardar"):
                c.execute("INSERT INTO viajes (fecha, cliente, patente, precio, estado_pago, destino) VALUES (?,?,?,?,?,?)",
                          (datetime.now().strftime("%d/%m/%Y"), f_cli, f_pat, f_pre, 'Pendiente', f_dest))
                conn.commit()
                st.success("¡Guardado!")

    # --- PESTAÑA 2: HISTORIAL ---
    with tabs[1]:
        df = pd.read_sql_query("SELECT * FROM viajes", conn)
        if st.session_state['rol'] == "Administrador":
            st.subheader("Edición de Viajes")
            df_ed = st.data_editor(df, num_rows="dynamic")
            if st.button("Actualizar Tabla"):
                df_ed.to_sql('viajes', conn, if_exists='replace', index=False)
                st.success("Cambios guardados")
        else:
            st.dataframe(df)

    # --- PESTAÑA 4: GESTIÓN DE USUARIOS (Solo Admin) ---
    if st.session_state['rol'] == "Administrador":
        with tabs[3]:
            st.header("Administrar Accesos")
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Crear Nuevo Usuario")
                new_u = st.text_input("Nombre de Usuario (ej: marcelo2)")
                new_p = st.text_input("Contraseña Nueva", type="password")
                new_r = st.selectbox("Rol", ["Operador", "Administrador"])
                if st.button("Registrar Usuario"):
                    if new_u and new_p:
                        try:
                            c.execute("INSERT INTO usuarios VALUES (?,?,?)", (new_u, new_p, new_r))
                            conn.commit()
                            st.success(f"Usuario {new_u} creado con éxito")
                            st.rerun()
                        except:
                            st.error("El usuario ya existe")
            
            with col2:
                st.subheader("Usuarios Actuales")
                users_df = pd.read_sql_query("SELECT user, rol FROM usuarios", conn)
                for i, row in users_df.iterrows():
                    c_u, c_r, c_b = st.columns([2,2,1])
                    c_u.write(row['user'])
                    c_r.write(row['rol'])
                    if row['user'] != 'admin': # Evita que borres el admin principal
                        if c_b.button("Borrar", key=f"del_{row['user']}"):
                            c.execute("DELETE FROM usuarios WHERE user=?", (row['user'],))
                            conn.commit()
                            st.rerun()
