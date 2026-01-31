import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

# Base de datos Pro con Vendedores
conn = sqlite3.connect('logistica_marcelo_online.db', check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS vehiculos 
             (id INTEGER PRIMARY KEY, patente TEXT UNIQUE, modelo TEXT, rendimiento REAL)''')

c.execute('''CREATE TABLE IF NOT EXISTS viajes 
             (id INTEGER PRIMARY KEY, fecha TEXT, chofer TEXT, patente TEXT, 
              cliente TEXT, vendedor TEXT, unidades INTEGER, precio_total REAL,
              origen TEXT, destino TEXT, km REAL, bano_id TEXT)''')
conn.commit()

st.set_page_config(page_title="Sistema Baños Online", layout="wide")

st.title("🌐 Gestión de Baños Químicos - Marcelo")
tab_viajes, tab_flota, tab_ventas = st.tabs(["📝 Nuevo Pedido", "🚛 Flota", "📊 Reporte de Ventas"])

# --- REGISTRO ---
with tab_viajes:
    vehiculos_lista = pd.read_sql_query("SELECT patente FROM vehiculos", conn)['patente'].tolist()
    
    with st.form("form_online"):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_cliente = st.text_input("Cliente")
            f_vendedor = st.selectbox("Vendido por", ["Marcelo", "Vendedor A", "Vendedor B", "Web"])
            f_unidades = st.number_input("Unidades", min_value=1)
        with c2:
            f_precio = st.number_input("Precio ($)", min_value=0.0)
            f_chofer = st.selectbox("Chofer", ["Marcelo", "Empleado 1"])
            f_vehiculo = st.selectbox("Vehículo", vehiculos_lista if vehiculos_lista else ["Cargar Vehículo"])
        with c3:
            f_destino = st.text_input("Dirección de Entrega")
            f_bano_id = st.text_input("Nro de Serie Baño/s")
            
        if st.form_submit_button("Guardar y Sincronizar"):
            try:
                # Lógica de GPS
                geolocator = Nominatim(user_agent="marcelo_pro")
                l1, l2 = geolocator.geocode("Córdoba, Argentina"), geolocator.geocode(f_destino)
                dist = round(geodesic((l1.latitude, l1.longitude), (l2.latitude, l2.longitude)).km * 1.3, 2) if l2 else 0
                
                fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
                c.execute('''INSERT INTO viajes (fecha, chofer, patente, cliente, vendedor, unidades, precio_total, origen, destino, km, bano_id) 
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                          (fecha, f_chofer, f_vehiculo, f_cliente, f_vendedor, f_unidades, f_precio, "Base", f_destino, dist, f_bano_id))
                conn.commit()
                st.success("✅ Datos guardados y disponibles online")
            except:
                st.error("Error al calcular ruta.")

# --- REPORTES DE VENDEDORES ---
with tab_ventas:
    data = pd.read_sql_query("SELECT * FROM viajes", conn)
    if not data.empty:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Ventas por Vendedor")
            ventas_vendedor = data.groupby('vendedor')['precio_total'].sum()
            st.bar_chart(ventas_vendedor)
        with col2:
            st.subheader("Rendimiento (KM) por Chofer")
            km_chofer = data.groupby('chofer')['km'].sum()
            st.bar_chart(km_chofer)
        
        st.subheader("Listado Maestro")
        st.dataframe(data)