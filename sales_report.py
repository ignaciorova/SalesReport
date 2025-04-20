import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import pdfkit
import base64
from io import BytesIO
import unicodedata
import hashlib

# Configuración de la página
st.set_page_config(page_title="Sistema de Reportes de Ventas - ASEAVNA", layout="wide")

# Clase para manejar contactos
class Contacto:
    def __init__(self, nombre, cedula, puesto, tipo):
        self.nombre = nombre if pd.notna(nombre) else "Desconocido"
        self.cedula = str(cedula) if pd.notna(cedula) else "Desconocido"
        self.puesto = puesto if pd.notna(puesto) else "No especificado"
        self.tipo = tipo if pd.notna(tipo) else "Desconocido"
        self.normalized_name = self._normalize_name(self.nombre)

    def _normalize_name(self, name):
        if not isinstance(name, str):
            return ''
        name = name.strip().lower()
        name = ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
        return name.replace(' ', '')

    def to_dict(self):
        return {
            'name': self.nombre,
            'cedula': self.cedula,
            'position': self.puesto,
            'tipo': self.tipo,
            'normalized_name': self.normalized_name
        }

# Clase para manejar una venta
class Venta:
    def __init__(self, cliente, empresa, fecha, orden, cantidad, precio_unitario, total, producto, vendedor, contacto):
        self.cliente = cliente if pd.notna(cliente) else "Desconocido"
        self.empresa = empresa if pd.notna(empresa) else ""
        try:
            self.fecha = pd.to_datetime(fecha, format='%Y-%m-%d %H:%M:%S')
        except:
            self.fecha = None
        self.orden = orden if pd.notna(orden) else ""
        self.cantidad = float(cantidad) if pd.notna(cantidad) else 0
        self.precio_unitario = float(precio_unitario) if pd.notna(precio_unitario) else 0
        self.total = float(total) if pd.notna(total) else 0
        self.producto = producto if pd.notna(producto) else ""
        self.vendedor = vendedor if pd.notna(vendedor) else ""

        client_parts = self.cliente.split(', ')
        self.tipo = ('BEN1_70' if 'BEN1_70' in client_parts[0] else
                     'BEN2_62' if 'BEN2_62' in client_parts[0] else
                     client_parts[0].replace('ASEAVNA ', ''))
        self.client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]

        self.contacto = contacto if contacto else Contacto(self.client_name, "Desconocido", "No especificado", self.tipo)
        self.name = self.contacto.nombre
        self.cedula = self.contacto.cedula
        self.position = self.contacto.puesto
        self.tipo = self.contacto.tipo if self.contacto.tipo != "Desconocido" else self.tipo

        if self.tipo == 'BEN1_70':
            self.cost_center = 'CostCenter_BEN1'
        elif self.tipo == 'BEN2_62':
            self.cost_center = 'CostCenter_BEN2'
        elif self.tipo in ['AVNA VISITAS', 'Contratista/Visitante']:
            self.cost_center = 'CostCenter_Visitante'
        elif self.tipo in ['AVNA GB', 'AVNA ONBOARDING']:
            self.cost_center = 'CostCenter_AVNA'
        elif self.tipo == 'Practicante':
            self.cost_center = 'CostCenter_Practicante'
        else:
            self.cost_center = 'CostCenter_Other'

        self.is_subsidized = (self.producto == 'Almuerzo Ejecutivo Aseavna')
        self.subsidy = 0
        self.employee_payment = self.total
        self.asoavna_contribution = 0
        self.asoavna_commission = 0
        self.client_credit = 0
        self.aseavna_account = 0

    def aplicar_subsidios_y_comisiones(self):
        if self.is_subsidized:
            if self.tipo == 'BEN1_70':
                self.subsidy = 2100
                self.employee_payment = 1000
                self.asoavna_contribution = 155
            elif self.tipo == 'BEN2_62':
                self.subsidy = 1800
                self.employee_payment = 1300
                self.asoavna_contribution = 155
            elif self.tipo in ['AVNA VISITAS', 'Contratista/Visitante', 'AVNA GB', 'AVNA ONBOARDING', 'Practicante']:
                self.subsidy = self.total
                self.employee_payment = 0
                self.asoavna_contribution = 0
        else:
            self.subsidy = 0
            self.employee_payment = self.total
            self.asoavna_contribution = 0
            self.asoavna_commission = self.total * 0.05

        self.client_credit = self.employee_payment
        self.aseavna_account = self.subsidy + self.asoavna_contribution + self.asoavna_commission

    def to_dict(self):
        return {
            'client': self.cliente,
            'name': self.name,
            'company': self.empresa,
            'date': self.fecha,
            'order': self.orden,
            'quantity': self.cantidad,
            'unit_price': self.precio_unitario,
            'total': self.total,
            'product': self.producto,
            'seller': self.vendedor,
            'cedula': self.cedula,
            'position': self.position,
            'tipo': self.tipo,
            'cost_center': self.cost_center,
            'is_subsidized': self.is_subsidized,
            'subsidy': self.subsidy,
            'employee_payment': self.employee_payment,
            'asoavna_contribution': self.asoavna_contribution,
            'asoavna_commission': self.asoavna_commission,
            'client_credit': self.client_credit,
            'aseavna_account': self.aseavna_account
        }

# Clase para manejar el reporte de ventas
class ReporteVentas:
    def __init__(self, sales_df, user_df):
        self.contactos = self._procesar_contactos(user_df)
        self.ventas = self._procesar_ventas(sales_df)
        self._aplicar_subsidios_y_comisiones()
        self.datos = self._crear_dataframe()
        self.facturacion = self._calcular_facturacion()
        self.comisiones_no_subsidiadas = self._calcular_comisiones_no_subsidiadas()
        self.reportes_individuales = self._generar_reportes_individuales()

    def _procesar_contactos(self, user_df):
        required_columns = ['Nombre', 'Cédula', 'Puesto', 'Tipo']
        missing_columns = [col for col in required_columns if col not in user_df.columns]
        if missing_columns:
            raise ValueError(f"Columnas faltantes en users_data.csv: {', '.join(missing_columns)}")

        user_df = user_df[user_df['Nombre'].notna() & (user_df['Nombre'].str.strip() != '')].copy()
        contactos = {}
        for _, row in user_df.iterrows():
            contacto = Contacto(row['Nombre'], row['Cédula'], row['Puesto'], row['Tipo'])
            contactos[contacto.normalized_name] = contacto
        return contactos

    def _procesar_ventas(self, sales_df):
        ventas = []
        for _, row in sales_df.iterrows():
            cliente = row['Cliente']
            client_parts = cliente.split(', ')
            client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]
            normalized_client_name = Contacto(client_name, None, None, None).normalized_name
            contacto = self.contactos.get(normalized_client_name, None)

            venta = Venta(
                cliente=row['Cliente'],
                empresa=row['Empresa'],
                fecha=row['Fecha de la orden'],
                orden=row['Orden'],
                cantidad=row['Cant. ordenada'],
                precio_unitario=row['Precio unitario'],
                total=row['Total'],
                producto=row['Variante del producto'],
                vendedor=row['Vendedor'],
                contacto=contacto
            )
            if venta.fecha and venta.total != 0:
                ventas.append(venta)
        return ventas

    def _aplicar_subsidios_y_comisiones(self):
        for venta in self.ventas:
            venta.aplicar_subsidios_y_comisiones()

    def _crear_dataframe(self):
        datos = [venta.to_dict() for venta in self.ventas]
        df = pd.DataFrame(datos)
        df['key'] = df['order'] + '-' + df['client'] + '-' + df['product']
        df = df.drop_duplicates(subset='key').drop(columns='key')
        return df

    def _calcular_facturacion(self):
        df = self.datos
        facturacion = {
            'BEN1_70': {'avna': 0, 'aseavna': 0, 'count': 0},
            'BEN2_62': {'avna': 0, 'aseavna': 0, 'count': 0},
            'Otros': {'avna': 0, 'aseavna': 0, 'count': 0}
        }

        for _, row in df.iterrows():
            if not row['is_subsidized']:
                continue
            if row['tipo'] == 'BEN1_70':
                facturacion['BEN1_70']['avna'] += row['subsidy'] * row['quantity']
                facturacion['BEN1_70']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion['BEN1_70']['count'] += row['quantity']
            elif row['tipo'] == 'BEN2_62':
                facturacion['BEN2_62']['avna'] += row['subsidy'] * row['quantity']
                facturacion['BEN2_62']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion['BEN2_62']['count'] += row['quantity']
            else:
                facturacion['Otros']['avna'] += row['subsidy'] * row['quantity']
                facturacion['Otros']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion['Otros']['count'] += row['quantity']

        total_transacciones_almuerzo = facturacion['BEN1_70']['count'] + facturacion['BEN2_62']['count']
        aseavna_contribution = total_transacciones_almuerzo * 155
        facturar_aseavna = (facturacion['BEN1_70']['aseavna'] + facturacion['BEN2_62']['aseavna'] +
                            facturacion['Otros']['aseavna'] + aseavna_contribution)

        return {
            'facturacion': facturacion,
            'aseavna_contribution': aseavna_contribution,
            'facturar_aseavna': facturar_aseavna
        }

    def _calcular_comisiones_no_subsidiadas(self):
        df = self.datos
        comisiones = []
        for _, row in df.iterrows():
            if not row['is_subsidized']:
                comisiones.append({
                    'client': row['client'],
                    'product': row['product'],
                    'total': row['total'] * row['quantity'],
                    'asoavna_commission': row['asoavna_commission'] * row['quantity']
                })
        return pd.DataFrame(comisiones)

    def _generar_reportes_individuales(self):
        df = self.datos
        reportes = {}
        for client, group in df.groupby('client'):
            total_client_credit = (group['client_credit'] * group['quantity']).sum()
            total_aseavna_account = (group['aseavna_account'] * group['quantity']).sum()
            subsidized = group[group['is_subsidized']].copy()
            non_subsidized = group[~group['is_subsidized']].copy()
            reportes[client] = {
                'transacciones': group,
                'subsidized': subsidized,
                'non_subsidized': non_subsidized,
                'total_client_credit': total_client_credit,
                'total_aseavna_account': total_aseavna_account
            }
        return reportes

    def aggregate_data(self, filtered_df):
        # Ingresos por cliente
        revenue_by_client = (filtered_df.groupby('client')
                            .agg({'total': 'sum', 'quantity': 'sum'})
                            .apply(lambda x: x['total'] * x['quantity'], axis=1)
                            .to_dict())

        # Ventas por fecha
        sales_by_date_df = (filtered_df.groupby(filtered_df['date'].dt.strftime('%Y-%m-%d'))
                           .agg({'total': 'sum', 'quantity': 'sum'}))
        sales_by_date = (sales_by_date_df['total'] * sales_by_date_df['quantity']).to_dict()

        # Distribución de productos
        product_distribution = filtered_df.groupby('product')['quantity'].sum().to_dict()

        # Consumo por contacto
        consumption_by_contact = filtered_df.groupby('client').apply(lambda x: x.to_dict('records')).to_dict()

        # Desglose de costos por tipo
        cost_breakdown_by_tipo = (filtered_df.groupby('tipo')
                                 .agg({'subsidy': 'sum', 'employee_payment': 'sum', 'quantity': 'sum'})
                                 .reset_index())
        cost_breakdown_by_tipo['subsidy'] = cost_breakdown_by_tipo['subsidy'] * cost_breakdown_by_tipo['quantity']
        cost_breakdown_by_tipo['employee_payment'] = cost_breakdown_by_tipo['employee_payment'] * cost_breakdown_by_tipo['quantity']
        cost_breakdown_by_tipo['count'] = cost_breakdown_by_tipo['quantity']

        return {
            'revenue_by_client': revenue_by_client,
            'sales_by_date': sales_by_date,
            'product_distribution': product_distribution,
            'consumption_by_contact': consumption_by_contact,
            'cost_breakdown_by_tipo': cost_breakdown_by_tipo
        }

# Formatear números con abreviaturas
def format_number(num):
    if not isinstance(num, (int, float)) or pd.isna(num):
        return '0'
    num = float(num)
    if abs(num) >= 1000000:
        return f"{num / 1000000:.1f}M"
    if abs(num) >= 1000:
        return f"{num / 1000:.1f}K"
    return f"{num:.0f}"

# Cargar datos
def load_data():
    try:
        sales_df = pd.read_csv('sales_data.csv')
        user_df = pd.read_csv('users_data.csv')
        return sales_df, user_df
    except Exception as e:
        st.error(f"Ocurrió un error al cargar los datos: {e}. Asegúrate de que los archivos sales_data.csv y users_data.csv estén disponibles y tengan el formato correcto.")
        return None, None

# Sistema de Login
def check_login(username, password):
    stored_users = {
        'admin': hashlib.sha256('admin123'.encode()).hexdigest()
    }
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    return username in stored_users and stored_users[username] == hashed_password

# Main app
def main():
    # Sistema de Login
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False

    # Título y descripción
    st.title("Sistema de Reportes de Ventas - ASEAVNA")
    st.markdown("**Generado el 19 de abril de 2025 para C2-ASEAVNA, Grecia, Costa Rica**")
    st.markdown("Sistema profesional para la gestión de ventas, subsidios y comisiones.")

    # Crear pestañas
    tabs = st.tabs(["Login", "Facturación", "Gráficos", "Historial de Consumo", "Reporte Individual", "Comisiones No Subsidiadas"])

    # Pestaña de Login
    with tabs[0]:
        if not st.session_state.logged_in:
            st.header("Iniciar Sesión")
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Iniciar Sesión"):
                if check_login(username, password):
                    st.session_state.logged_in = True
                    st.success("Inicio de sesión exitoso")
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos")
        else:
            st.success("Ya has iniciado sesión.")
            if st.button("Cerrar Sesión"):
                st.session_state.logged_in = False
                st.rerun()

    # Si no está logueado, no mostrar el resto de las pestañas
    if not st.session_state.logged_in:
        return

    # Cargar datos y almacenar en session_state
    if 'loaded_data' not in st.session_state:
        sales_df, user_df = load_data()
        st.session_state.loaded_data = (sales_df, user_df)
    else:
        sales_df, user_df = st.session_state.loaded_data

    if sales_df is None or user_df is None:
        return

    # Procesar datos con clases y almacenar en session_state
    if 'reporte' not in st.session_state:
        try:
            st.session_state.reporte = ReporteVentas(sales_df, user_df)
        except Exception as e:
            st.error(f"Error al procesar los datos: {e}")
            return

    reporte = st.session_state.reporte
    sales_data = reporte.datos
    facturacion = reporte.facturacion
    comisiones_no_subsidiadas = reporte.comisiones_no_subsidiadas
    reportes_individuales = reporte.reportes_individuales

    # Estado para filtros
    if 'selected_tipo' not in st.session_state:
        st.session_state.selected_tipo = 'All'
    if 'date_range_start' not in st.session_state:
        st.session_state.date_range_start = sales_data['date'].min().date()
    if 'date_range_end' not in st.session_state:
        st.session_state.date_range_end = sales_data['date'].max().date()
    if 'search_query' not in st.session_state:
        st.session_state.search_query = ''
    if 'selected_cost_center' not in st.session_state:
        st.session_state.selected_cost_center = 'All'
    if 'selected_client' not in st.session_state:
        st.session_state.selected_client = 'All'
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 1
    if 'sort_key' not in st.session_state:
        st.session_state.sort_key = 'date'
    if 'sort_direction' not in st.session_state:
        st.session_state.sort_direction = 'asc'
    if 'export_options' not in st.session_state:
        st.session_state.export_options = {
            'revenue_chart': True,
            'sales_trend': True,
            'product_pie': True,
            'cost_breakdown': True,
            'consumption_table': True,
            'facturacion_table': True,
            'individual_report': True,
            'non_subsidized_commissions': True
        }

    # Filtrar datos
    filtered_data = sales_data.copy()
    if st.session_state.selected_tipo != 'All':
        filtered_data = filtered_data[filtered_data['tipo'] == st.session_state.selected_tipo]
    if st.session_state.date_range_start and st.session_state.date_range_end:
        filtered_data = filtered_data[
            (filtered_data['date'].dt.date >= st.session_state.date_range_start) &
            (filtered_data['date'].dt.date <= st.session_state.date_range_end)
        ]
    if st.session_state.search_query:
        filtered_data = filtered_data[
            filtered_data['client'].str.lower().str.contains(st.session_state.search_query.lower(), na=False) |
            filtered_data['cedula'].str.lower().str.contains(st.session_state.search_query.lower(), na=False)
        ]
    if st.session_state.selected_cost_center != 'All':
        filtered_data = filtered_data[filtered_data['cost_center'] == st.session_state.selected_cost_center]
    if st.session_state.selected_client != 'All':
        filtered_data = filtered_data[filtered_data['client'] == st.session_state.selected_client]

    # Filtrar comisiones no subsidiadas
    filtered_comisiones = comisiones_no_subsidiadas.copy()
    if st.session_state.selected_client != 'All':
        filtered_comisiones = filtered_comisiones[filtered_comisiones['client'] == st.session_state.selected_client]

    # Ordenar datos
    filtered_data = filtered_data.sort_values(
        by=st.session_state.sort_key,
        ascending=(st.session_state.sort_direction == 'asc')
    )

    # Agregar datos (usar datos filtrados para gráficos)
    aggregated = reporte.aggregate_data(filtered_data)

    # Preparar datos para gráficos
    revenue_chart_data = pd.DataFrame([
        {'client': k, 'revenue': v} for k, v in aggregated['revenue_by_client'].items()
    ])
    total_revenue = revenue_chart_data['revenue'].sum() if not revenue_chart_data.empty else 0
    revenue_chart_data['percentage'] = (revenue_chart_data['revenue'] / total_revenue * 100).round(1) if total_revenue > 0 else 0
    revenue_chart_data['client'] = revenue_chart_data['client'].apply(
        lambda x: x[:17] + '...' if len(x) > 20 else x
    )

    sales_trend_data = pd.DataFrame([
        {'date': k, 'revenue': v} for k, v in aggregated['sales_by_date'].items()
    ]).sort_values('date')
    # Reducir datos para el gráfico de tendencia (muestreo si hay muchos puntos)
    if len(sales_trend_data) > 100:
        sales_trend_data = sales_trend_data.iloc[::len(sales_trend_data)//100]

    product_pie_data = pd.DataFrame([
        {'name': k, 'value': v} for k, v in aggregated['product_distribution'].items()
    ])

    cost_breakdown_data = aggregated['cost_breakdown_by_tipo']

    # Filtros (mostrar en todas las pestañas excepto Login)
    with tabs[1], tabs[2], tabs[3], tabs[4], tabs[5]:
        st.header("Filtros de Reporte")
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            unique_tipos = ['All'] + sorted(sales_data['tipo'].unique())
            selected_tipo = st.selectbox("Tipo", unique_tipos, index=unique_tipos.index(st.session_state.selected_tipo), key="tipo_filter")
        with col2:
            start_date, end_date = st.date_input(
                "Rango de Fechas",
                [st.session_state.date_range_start, st.session_state.date_range_end],
                min_value=sales_data['date'].min().date(),
                max_value=sales_data['date'].max().date(),
                key="date_filter"
            )
        with col3:
            search_query = st.text_input("Buscar Cliente o Cédula", value=st.session_state.search_query, key="search_filter")
        with col4:
            unique_cost_centers = ['All'] + sorted(sales_data['cost_center'].unique())
            selected_cost_center = st.selectbox("Centro de Costos", unique_cost_centers, index=unique_cost_centers.index(st.session_state.selected_cost_center), key="cost_center_filter")
        with col5:
            unique_clients = ['All'] + sorted(sales_data['client'].unique())
            selected_client = st.selectbox("Cliente", unique_clients, index=unique_clients.index(st.session_state.selected_client), key="client_filter")

        # Actualizar filtros solo si cambian
        filters_changed = (
            selected_tipo != st.session_state.selected_tipo or
            start_date != st.session_state.date_range_start or
            end_date != st.session_state.date_range_end or
            search_query != st.session_state.search_query or
            selected_cost_center != st.session_state.selected_cost_center or
            selected_client != st.session_state.selected_client
        )
        if filters_changed:
            st.session_state.selected_tipo = selected_tipo
            st.session_state.date_range_start = start_date
            st.session_state.date_range_end = end_date
            st.session_state.search_query = search_query
            st.session_state.selected_cost_center = selected_cost_center
            st.session_state.selected_client = selected_client
            st.session_state.current_page = 1  # Resetear página al cambiar filtros
            st.rerun()

        if st.button("Restablecer Filtros"):
            st.session_state.selected_tipo = 'All'
            st.session_state.date_range_start = sales_data['date'].min().date()
            st.session_state.date_range_end = sales_data['date'].max().date()
            st.session_state.search_query = ''
            st.session_state.selected_cost_center = 'All'
            st.session_state.selected_client = 'All'
            st.session_state.current_page = 1
            st.rerun()

    # Pestaña de Facturación
    with tabs[1]:
        st.header("Desglose de Facturación (solo Almuerzo Ejecutivo Aseavna)")
        st.write("Nota: Los subsidios y costos asociados se aplican únicamente al producto 'Almuerzo Ejecutivo Aseavna'.")

        facturacion_filtered = {
            'BEN1_70': {'avna': 0, 'aseavna': 0, 'count': 0},
            'BEN2_62': {'avna': 0, 'aseavna': 0, 'count': 0},
            'Otros': {'avna': 0, 'aseavna': 0, 'count': 0}
        }
        for _, row in filtered_data.iterrows():
            if not row['is_subsidized']:
                continue
            if row['tipo'] == 'BEN1_70':
                facturacion_filtered['BEN1_70']['avna'] += row['subsidy'] * row['quantity']
                facturacion_filtered['BEN1_70']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion_filtered['BEN1_70']['count'] += row['quantity']
            elif row['tipo'] == 'BEN2_62':
                facturacion_filtered['BEN2_62']['avna'] += row['subsidy'] * row['quantity']
                facturacion_filtered['BEN2_62']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion_filtered['BEN2_62']['count'] += row['quantity']
            else:
                facturacion_filtered['Otros']['avna'] += row['subsidy'] * row['quantity']
                facturacion_filtered['Otros']['aseavna'] += row['employee_payment'] * row['quantity']
                facturacion_filtered['Otros']['count'] += row['quantity']

        total_transacciones_almuerzo = facturacion_filtered['BEN1_70']['count'] + facturacion_filtered['BEN2_62']['count']
        aseavna_contribution = total_transacciones_almuerzo * 155
        non_subsidized_commission = filtered_comisiones['asoavna_commission'].sum()
        total_facturar_aseavna = (facturacion_filtered['BEN1_70']['aseavna'] +
                                 facturacion_filtered['BEN2_62']['aseavna'] +
                                 facturacion_filtered['Otros']['aseavna'] +
                                 aseavna_contribution +
                                 non_subsidized_commission)

        facturacion_df = pd.DataFrame([
            {'Concepto': 'Facturar a AVNA (BEN1_70)', 'Monto (₡)': format_number(facturacion_filtered['BEN1_70']['avna']), 'Transacciones': facturacion_filtered['BEN1_70']['count']},
            {'Concepto': 'Pagar a Aseavna (BEN1_70)', 'Monto (₡)': format_number(facturacion_filtered['BEN1_70']['aseavna']), 'Transacciones': facturacion_filtered['BEN1_70']['count']},
            {'Concepto': 'Facturar a AVNA (BEN2_62)', 'Monto (₡)': format_number(facturacion_filtered['BEN2_62']['avna']), 'Transacciones': facturacion_filtered['BEN2_62']['count']},
            {'Concepto': 'Pagar a Aseavna (BEN2_62)', 'Monto (₡)': format_number(facturacion_filtered['BEN2_62']['aseavna']), 'Transacciones': facturacion_filtered['BEN2_62']['count']},
            {'Concepto': 'Facturar a AVNA (Otros)', 'Monto (₡)': format_number(facturacion_filtered['Otros']['avna']), 'Transacciones': facturacion_filtered['Otros']['count']},
            {'Concepto': 'Pagar a Aseavna (Otros)', 'Monto (₡)': format_number(facturacion_filtered['Otros']['aseavna']), 'Transacciones': facturacion_filtered['Otros']['count']},
            {'Concepto': 'Contribución Aseavna (5%)', 'Monto (₡)': format_number(aseavna_contribution), 'Transacciones': ''},
            {'Concepto': 'Comisión Productos No Subsidiados (5%)', 'Monto (₡)': format_number(non_subsidized_commission), 'Transacciones': ''},
            {'Concepto': 'Total a Facturar a Aseavna', 'Monto (₡)': format_number(total_facturar_aseavna), 'Transacciones': ''}
        ])
        st.dataframe(facturacion_df, use_container_width=True)

    # Pestaña de Gráficos
    with tabs[2]:
        if st.session_state.export_options['revenue_chart']:
            st.header("Ingresos por Cliente")
            if not revenue_chart_data.empty:
                fig = px.bar(revenue_chart_data, x='client', y='revenue', text='percentage',
                             labels={'revenue': 'Ingresos (₡)', 'client': 'Cliente', 'percentage': 'Porcentaje (%)'},
                             color_discrete_sequence=['#1F77B4'])
                fig.update_traces(texttemplate='%{text}%', textposition='outside')
                fig.update_layout(yaxis_tickformat=',.0f')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.write("No hay datos para mostrar con los filtros actuales.")

        if st.session_state.export_options['sales_trend']:
            st.header("Tendencia de Ventas Diarias")
            if not sales_trend_data.empty:
                fig = px.line(sales_trend_data, x='date', y='revenue',
                              labels={'revenue': 'Ingresos (₡)', 'date': 'Fecha'},
                              color_discrete_sequence=['#FF7F0E'])
                fig.update_layout(yaxis_tickformat=',.0f')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.write("No hay datos para mostrar con los filtros actuales.")

        if st.session_state.export_options['product_pie']:
            st.header("Distribución de Productos")
            if not product_pie_data.empty:
                fig = px.pie(product_pie_data, names='name', values='value',
                             color_discrete_sequence=['#2CA02C', '#D62728', '#9467BD'])
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.write("No hay datos para mostrar con los filtros actuales.")

        if st.session_state.export_options['cost_breakdown']:
            st.header("Desglose de Costos por Tipo")
            if not cost_breakdown_data.empty:
                fig = go.Figure(data=[
                    go.Bar(name='Subsidio', x=cost_breakdown_data['tipo'], y=cost_breakdown_data['subsidy'], marker_color='#1F77B4'),
                    go.Bar(name='Pago Empleado', x=cost_breakdown_data['tipo'], y=cost_breakdown_data['employee_payment'], marker_color='#FF7F0E')
                ])
                fig.update_layout(barmode='stack', yaxis_title='Monto (₡)', xaxis_title='Tipo', yaxis_tickformat=',.0f')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.write("No hay datos para mostrar con los filtros actuales.")

    # Pestaña de Historial de Consumo
    with tabs[3]:
        st.header("Historial de Consumo por Contacto")
        rows_per_page = 50
        total_pages = (len(filtered_data) + rows_per_page - 1) // rows_per_page
        st.session_state.current_page = max(1, min(st.session_state.current_page, total_pages))

        start_idx = (st.session_state.current_page - 1) * rows_per_page
        end_idx = min(start_idx + rows_per_page, len(filtered_data))
        paginated_data = filtered_data.iloc[start_idx:end_idx].copy()
        paginated_data['date'] = paginated_data['date'].dt.strftime('%Y-%m-%d')
        paginated_data['total'] = paginated_data['total'].apply(format_number)
        paginated_data['subsidy'] = paginated_data['subsidy'].apply(format_number)
        paginated_data['employee_payment'] = paginated_data['employee_payment'].apply(format_number)
        paginated_data['asoavna_contribution'] = paginated_data['asoavna_contribution'].apply(format_number)
        paginated_data['asoavna_commission'] = paginated_data['asoavna_commission'].apply(format_number)
        paginated_data['client_credit'] = paginated_data['client_credit'].apply(format_number)
        paginated_data['aseavna_account'] = paginated_data['aseavna_account'].apply(format_number)

        sort_options = {
            'Cliente': 'client',
            'Nombre Vinculado': 'name',
            'Cédula': 'cedula',
            'Puesto': 'position',
            'Tipo': 'tipo',
            'Fecha': 'date',
            'Producto': 'product',
            'Cantidad': 'quantity',
            'Total (₡)': 'total',
            'Subsidio (₡)': 'subsidy',
            'Pago Empleado (₡)': 'employee_payment',
            'Centro de Costos': 'cost_center',
            'Crédito Cliente (₡)': 'client_credit',
            'Cuenta Aseavna (₡)': 'aseavna_account',
            'Comisión Aseavna (₡)': 'asoavna_commission'
        }
        col_sort = st.columns(2)
        with col_sort[0]:
            sort_by = st.selectbox("Ordenar por", list(sort_options.keys()))
        with col_sort[1]:
            direction = st.selectbox("Dirección", ['Ascendente', 'Descendente'])
        
        if sort_by != [k for k, v in sort_options.items() if v == st.session_state.sort_key][0] or \
           direction != ('Ascendente' if st.session_state.sort_direction == 'asc' else 'Descendente'):
            st.session_state.sort_key = sort_options[sort_by]
            st.session_state.sort_direction = 'asc' if direction == 'Ascendente' else 'desc'
            st.rerun()

        st.dataframe(paginated_data[[
            'client', 'name', 'cedula', 'position', 'tipo', 'date', 'product',
            'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center',
            'is_subsidized', 'client_credit', 'aseavna_account', 'asoavna_commission'
        ]], use_container_width=True)

        col_pagination = st.columns(3)
        with col_pagination[0]:
            if st.button("Anterior"):
                st.session_state.current_page = max(1, st.session_state.current_page - 1)
                st.rerun()
        with col_pagination[1]:
            st.write(f"Página {st.session_state.current_page} de {total_pages}")
        with col_pagination[2]:
            if st.button("Siguiente"):
                st.session_state.current_page = min(total_pages, st.session_state.current_page + 1)
                st.rerun()

    # Pestaña de Reporte Individual
    with tabs[4]:
        if st.session_state.selected_client != 'All':
            st.header(f"Reporte Individual: {st.session_state.selected_client}")
            client_data = reportes_individuales.get(st.session_state.selected_client, None)
            if client_data:
                col_client = st.columns(2)
                with col_client[0]:
                    st.metric("Total en Cuenta de Crédito del Cliente", f"₡{format_number(client_data['total_client_credit'])}")
                with col_client[1]:
                    st.metric("Total en Cuenta de Aseavna", f"₡{format_number(client_data['total_aseavna_account'])}")

                st.subheader("Transacciones Subsidiadas (Almuerzo Ejecutivo Aseavna)")
                subsidized_df = client_data['subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_contribution', 'client_credit', 'aseavna_account']]
                subsidized_df['date'] = subsidized_df['date'].dt.strftime('%Y-%m-%d')
                subsidized_df['total'] = subsidized_df['total'].apply(format_number)
                subsidized_df['subsidy'] = subsidized_df['subsidy'].apply(format_number)
                subsidized_df['employee_payment'] = subsidized_df['employee_payment'].apply(format_number)
                subsidized_df['asoavna_contribution'] = subsidized_df['asoavna_contribution'].apply(format_number)
                subsidized_df['client_credit'] = subsidized_df['client_credit'].apply(format_number)
                subsidized_df['aseavna_account'] = subsidized_df['aseavna_account'].apply(format_number)
                st.dataframe(subsidized_df, use_container_width=True)

                st.subheader("Transacciones No Subsidiadas")
                non_subsidized_df = client_data['non_subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account']]
                non_subsidized_df['date'] = non_subsidized_df['date'].dt.strftime('%Y-%m-%d')
                non_subsidized_df['total'] = non_subsidized_df['total'].apply(format_number)
                non_subsidized_df['subsidy'] = non_subsidized_df['subsidy'].apply(format_number)
                non_subsidized_df['employee_payment'] = non_subsidized_df['employee_payment'].apply(format_number)
                non_subsidized_df['asoavna_commission'] = non_subsidized_df['asoavna_commission'].apply(format_number)
                non_subsidized_df['client_credit'] = non_subsidized_df['client_credit'].apply(format_number)
                non_subsidized_df['aseavna_account'] = non_subsidized_df['aseavna_account'].apply(format_number)
                st.dataframe(non_subsidized_df, use_container_width=True)
        else:
            st.write("Selecciona un cliente para ver su reporte individual.")

    # Pestaña de Comisiones No Subsidiadas
    with tabs[5]:
        st.header("Comisiones de Productos No Subsidiados (5% Aseavna)")
        st.write("Nota: Se aplica una comisión del 5% a todos los productos no subsidiados (ej. Coca-Cola).")
        if not filtered_comisiones.empty:
            comisiones_display = filtered_comisiones.copy()
            comisiones_display['total'] = comisiones_display['total'].apply(format_number)
            comisiones_display['asoavna_commission'] = comisiones_display['asoavna_commission'].apply(format_number)
            st.dataframe(comisiones_display, use_container_width=True)
        else:
            st.write("No hay transacciones de productos no subsidiados con los filtros actuales.")

    # Opciones de Exportación (mostrar en todas las pestañas excepto Login)
    with tabs[1], tabs[2], tabs[3], tabs[4], tabs[5]:
        st.header("Opciones de Exportación")
        col_export = st.columns(8)
        with col_export[0]:
            st.session_state.export_options['revenue_chart'] = st.checkbox("Gráfico de Ingresos por Cliente", value=st.session_state.export_options['revenue_chart'])
        with col_export[1]:
            st.session_state.export_options['sales_trend'] = st.checkbox("Gráfico de Tendencia de Ventas", value=st.session_state.export_options['sales_trend'])
        with col_export[2]:
            st.session_state.export_options['product_pie'] = st.checkbox("Gráfico de Distribución de Productos", value=st.session_state.export_options['product_pie'])
        with col_export[3]:
            st.session_state.export_options['cost_breakdown'] = st.checkbox("Gráfico de Desglose de Costos", value=st.session_state.export_options['cost_breakdown'])
        with col_export[4]:
            st.session_state.export_options['consumption_table'] = st.checkbox("Tabla de Consumo", value=st.session_state.export_options['consumption_table'])
        with col_export[5]:
            st.session_state.export_options['facturacion_table'] = st.checkbox("Tabla de Facturación", value=st.session_state.export_options['facturacion_table'])
        with col_export[6]:
            st.session_state.export_options['individual_report'] = st.checkbox("Reporte Individual", value=st.session_state.export_options['individual_report'])
        with col_export[7]:
            st.session_state.export_options['non_subsidized_commissions'] = st.checkbox("Comisiones No Subsidiadas", value=st.session_state.export_options['non_subsidized_commissions'])

        col_btn = st.columns(3)
        with col_btn[0]:
            if st.button("Exportar a Excel"):
                buffer = BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    total_revenue = (filtered_data['total'] * filtered_data['quantity']).sum()
                    total_subsidies = (filtered_data['subsidy'] * filtered_data['quantity']).sum()
                    average_transaction = total_revenue / len(filtered_data) if len(filtered_data) > 0 else 0
                    summary_data = pd.DataFrame([
                        ['Métricas Principales', ''],
                        ['Ingresos Totales', f"₡{format_number(total_revenue)}"],
                        ['Subsidios Totales', f"₡{format_number(total_subsidies)}"],
                        ['Transacción Promedio', f"₡{format_number(average_transaction)}"],
                        ['Transacciones Totales', len(filtered_data)],
                        ['Clientes Únicos', filtered_data['client'].nunique()]
                    ], columns=['Métrica', 'Valor'])
                    summary_data.to_excel(writer, sheet_name='Resumen', index=False)

                    if st.session_state.export_options['consumption_table']:
                        export_df = filtered_data[['client', 'name', 'cedula', 'position', 'tipo', 'date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center', 'is_subsidized', 'client_credit', 'aseavna_account', 'asoavna_commission']]
                        export_df.to_excel(writer, sheet_name='Consumo', index=False)

                    if st.session_state.export_options['facturacion_table']:
                        facturacion_data = pd.DataFrame([
                            ['Facturar a AVNA (BEN1_70)', f"₡{format_number(facturacion_filtered['BEN1_70']['avna'])}", facturacion_filtered['BEN1_70']['count']],
                            ['Pagar a Aseavna (BEN1_70)', f"₡{format_number(facturacion_filtered['BEN1_70']['aseavna'])}", facturacion_filtered['BEN1_70']['count']],
                            ['Facturar a AVNA (BEN2_62)', f"₡{format_number(facturacion_filtered['BEN2_62']['avna'])}", facturacion_filtered['BEN2_62']['count']],
                            ['Pagar a Aseavna (BEN2_62)', f"₡{format_number(facturacion_filtered['BEN2_62']['aseavna'])}", facturacion_filtered['BEN2_62']['count']],
                            ['Facturar a AVNA (Otros)', f"₡{format_number(facturacion_filtered['Otros']['avna'])}", facturacion_filtered['Otros']['count']],
                            ['Pagar a Aseavna (Otros)', f"₡{format_number(facturacion_filtered['Otros']['aseavna'])}", facturacion_filtered['Otros']['count']],
                            ['Contribución Aseavna (5%)', f"₡{format_number(aseavna_contribution)}", ''],
                            ['Comisión Productos No Subsidiados (5%)', f"₡{format_number(non_subsidized_commission)}", ''],
                            ['Total a Facturar a Aseavna', f"₡{format_number(total_facturar_aseavna)}", '']
                        ], columns=['Concepto', 'Monto', 'Transacciones'])
                        facturacion_data.to_excel(writer, sheet_name='Facturación', index=False)

                    if st.session_state.export_options['individual_report']:
                        for client, datos in reportes_individuales.items():
                            client_df = datos['transacciones'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_contribution', 'client_credit', 'aseavna_account', 'asoavna_commission']]
                            client_df.to_excel(writer, sheet_name=f'Cliente_{client[:20]}', index=False)

                    if st.session_state.export_options['non_subsidized_commissions']:
                        comisiones_df = filtered_comisiones[['client', 'product', 'total', 'asoavna_commission']]
                        comisiones_df.to_excel(writer, sheet_name='Comisiones_No_Subsidiadas', index=False)

                buffer.seek(0)
                st.download_button(
                    label="Descargar Excel",
                    data=buffer,
                    file_name=f"reporte_ventas_aseavna_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        with col_btn[1]:
            if st.button("Exportar a CSV"):
                export_df = filtered_data[['client', 'name', 'cedula', 'position', 'tipo', 'date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center', 'is_subsidized', 'client_credit', 'aseavna_account', 'asoavna_commission']]
                csv = export_df.to_csv(index=False)
                st.download_button(
                    label="Descargar CSV",
                    data=csv,
                    file_name=f"reporte_ventas_aseavna_{datetime.now().strftime('%Y-%m-%d')}.csv",
                    mime="text/csv"
                )

        with col_btn[2]:
            if st.button("Exportar a PDF"):
                try:
                    pdfkit.from_string("Reporte de Ventas", "reporte_ventas.pdf")
                    with open("reporte_ventas.pdf", "rb") as f:
                        pdf_data = f.read()
                    st.download_button(
                        label="Descargar PDF",
                        data=pdf_data,
                        file_name=f"reporte_ventas_aseavna_{datetime.now().strftime('%Y-%m-%d')}.pdf",
                        mime="application/pdf"
                    )
                except Exception as e:
                    st.warning(f"No se pudo generar el PDF. Asegúrate de que pdfkit y wkhtmltopdf estén instalados. Error: {e}")

if __name__ == "__main__":
    main()