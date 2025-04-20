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
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

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
        client_parts = self.cliente.split(', ')
        self.display_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]

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
        elif self.tipo in ['AVNA GB']:
            self.cost_center = 'CostCenter_AVNA_GB'
        elif self.tipo in ['AVNA ONBOARDING']:
            self.cost_center = 'CostCenter_AVNA_Onboarding'
        elif self.tipo == 'Practicante':
            self.cost_center = 'CostCenter_Practicante'
        else:
            self.cost_center = 'CostCenter_Other'

        self.is_subsidized = (self.producto == 'Almuerzo Ejecutivo Aseavna')
        self.subsidy = 0
        self.employee_payment = self.total
        self.employee_payment_base = 0
        self.asoavna_commission = 0
        self.iva = 0
        self.client_credit = 0
        self.aseavna_account = 0
        self.base_price = 0

    def aplicar_subsidios_y_comisiones(self, iva_rate):
        # Calcular precio base considerando el IVA seleccionado
        iva_factor = 1 + (iva_rate / 100)
        self.base_price = self.total / iva_factor
        self.iva = self.total - self.base_price

        # Ajustar el precio base para "Almuerzo Ejecutivo Aseavna"
        if self.is_subsidized:
            # Para "Almuerzo Ejecutivo Aseavna", el precio total debe ser 3100
            self.total = 3100
            self.base_price = self.total / iva_factor
            self.iva = self.total - self.base_price

            # Subsidios y comisiones
            if self.tipo == 'BEN1_70':
                self.subsidy = 2100
                self.employee_payment = 1000
                self.employee_payment_base = self.employee_payment / iva_factor
                self.asoavna_commission = 155  # Comisión fija por transacción
            elif self.tipo == 'BEN2_62':
                self.subsidy = 1800
                self.employee_payment = 1300
                self.employee_payment_base = self.employee_payment / iva_factor
                self.asoavna_commission = 150  # Comisión fija por transacción
            else:
                # Para otros tipos (Visitas, etc.), no hay subsidio
                self.subsidy = 0
                self.employee_payment = self.total
                self.employee_payment_base = self.base_price
                self.asoavna_commission = self.total * 0.05  # 5% por transacción
        else:
            # Para productos no subsidiados (ej. frescos)
            self.subsidy = 0
            self.employee_payment = self.total
            self.employee_payment_base = self.base_price
            self.asoavna_commission = self.total * 0.05  # 5% por transacción

        self.client_credit = self.employee_payment
        self.aseavna_account = self.subsidy

    def to_dict(self):
        return {
            'client': self.cliente,
            'display_name': self.display_name,
            'name': self.name,
            'company': self.empresa,
            'date': self.fecha,
            'order': self.orden,
            'quantity': self.cantidad,
            'unit_price': self.precio_unitario,
            'total': self.total,
            'base_price': self.base_price,
            'product': self.producto,
            'seller': self.vendedor,
            'cedula': self.cedula,
            'position': self.position,
            'tipo': self.tipo,
            'cost_center': self.cost_center,
            'is_subsidized': self.is_subsidized,
            'subsidy': self.subsidy,
            'employee_payment': self.employee_payment,
            'employee_payment_base': self.employee_payment_base,
            'asoavna_commission': self.asoavna_commission,
            'iva': self.iva,
            'client_credit': self.client_credit,
            'aseavna_account': self.aseavna_account
        }

# Clase para manejar el reporte de ventas
class ReporteVentas:
    def __init__(self, sales_df, user_df, iva_rate):
        self.iva_rate = iva_rate
        self.contactos = self._procesar_contactos(user_df)
        self.ventas = self._procesar_ventas(sales_df)
        self._aplicar_subsidios_y_comisiones()
        self.datos = self._crear_dataframe()
        self.etiquetas_fila = self._generar_etiquetas_fila(self.datos)
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
            venta.aplicar_subsidios_y_comisiones(self.iva_rate)

    def _crear_dataframe(self):
        datos = [venta.to_dict() for venta in self.ventas]
        df = pd.DataFrame(datos)
        df['key'] = df['order'] + '-' + df['client'] + '-' + df['product']
        df = df.drop_duplicates(subset='key').drop(columns='key')
        return df

    def _generar_etiquetas_fila(self, df=None):
        if df is None:
            df = self.datos.copy()
        # Agrupar por cliente y producto
        grouped = df.groupby(['client', 'display_name', 'tipo', 'product']).agg({
            'quantity': 'sum',
            'client_credit': 'sum',
            'aseavna_account': 'sum'
        }).reset_index()

        # Crear una lista para las filas del reporte
        etiquetas_data = []
        current_tipo = None
        for _, row in grouped.sort_values(['tipo', 'client', 'product']).iterrows():
            # Si el tipo cambia, añadir un encabezado
            if row['tipo'] != current_tipo:
                etiquetas_data.append({
                    'Empleado': f"ASEAVNA {row['tipo']}",
                    'Producto': '',
                    'Suma de Cant. ordenada': '',
                    'Suma de Monto Cliente': '',
                    'Suma de Monto Subsidiado': ''
                })
                current_tipo = row['tipo']

            # Añadir la fila del empleado
            etiquetas_data.append({
                'Empleado': row['display_name'],
                'Producto': row['product'],
                'Suma de Cant. ordenada': row['quantity'],
                'Suma de Monto Cliente': row['client_credit'] * row['quantity'],
                'Suma de Monto Subsidiado': row['aseavna_account'] * row['quantity']
            })

        # Añadir filas "en blanco" si es necesario (para tipos sin transacciones)
        tipos_esperados = ['BEN1_70', 'BEN2_62', 'AVNA VISITAS']
        tipos_presentes = grouped['tipo'].unique()
        for tipo in tipos_esperados:
            if tipo not in tipos_presentes:
                etiquetas_data.append({
                    'Empleado': f"ASEAVNA {tipo}",
                    'Producto': '(en blanco)',
                    'Suma de Cant. ordenada': 0,
                    'Suma de Monto Cliente': 0,
                    'Suma de Monto Subsidiado': 0
                })

        # Añadir fila "Total general"
        total_quantity = grouped['quantity'].sum()
        total_client = (grouped['client_credit'] * grouped['quantity']).sum()
        total_subsidized = (grouped['aseavna_account'] * grouped['quantity']).sum()
        etiquetas_data.append({
            'Empleado': 'Total general',
            'Producto': '',
            'Suma de Cant. ordenada': total_quantity,
            'Suma de Monto Cliente': total_client,
            'Suma de Monto Subsidiado': total_subsidized
        })

        return pd.DataFrame(etiquetas_data)

    def _calcular_facturacion(self):
        df = self.datos
        facturacion = {
            'BEN1_70': {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0},
            'BEN2_62': {'count': 0, 'subsidy': 0objeto, 'employee_payment': 0, 'iva': 0, 'commission': 0},
            'Otros': {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0}
        }

        total_commission = 0
        for _, row in df.iterrows():
            total_commission += row['asoavna_commission'] * row['quantity']
            if not row['is_subsidized']:
                continue
            if row['tipo'] == 'BEN1_70':
                facturacion['BEN1_70']['count'] += row['quantity']
                facturacion['BEN1_70']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion['BEN1_70']['employee_payment'] += row['employee_payment_base'] * row['quantity']
                facturacion['BEN1_70']['iva'] += row['iva'] * row['quantity']
                facturacion['BEN1_70']['commission'] += row['asoavna_commission'] * row['quantity']
            elif row['tipo'] == 'BEN2_62':
                facturacion['BEN2_62']['count'] += row['quantity']
                facturacion['BEN2_62']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion['BEN2_62']['employee_payment'] += row['employee_payment_base'] * row['quantity']
                facturacion['BEN2_62']['iva'] += row['iva'] * row['quantity']
                facturacion['BEN2_62']['commission'] += row['asoavna_commission'] * row['quantity']
            else:
                facturacion['Otros']['count'] += row['quantity']
                facturacion['Otros']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion['Otros']['employee_payment'] += row['employee_payment'] * row['quantity']
                facturacion['Otros']['iva'] += row['iva'] * row['quantity']
                facturacion['Otros']['commission'] += row['asoavna_commission'] * row['quantity']

        # Calcular porcentajes de subsidio
        total_ben1 = facturacion['BEN1_70']['subsidy'] + facturacion['BEN1_70']['employee_payment']
        total_ben2 = facturacion['BEN2_62']['subsidy'] + facturacion['BEN2_62']['employee_payment']
        subsidy_percentage_ben1 = (facturacion['BEN1_70']['subsidy'] / total_ben1 * 100) if total_ben1 > 0 else 0
        subsidy_percentage_ben2 = (facturacion['BEN2_62']['subsidy'] / total_ben2 * 100) if total_ben2 > 0 else 0

        # Calcular totales
        total_subsidy = facturacion['BEN1_70']['subsidy'] + facturacion['BEN2_62']['subsidy'] + facturacion['Otros']['subsidy']
        total_employee_payment = facturacion['BEN1_70']['employee_payment'] + facturacion['BEN2_62']['employee_payment'] + facturacion['Otros']['employee_payment']
        total_iva = facturacion['BEN1_70']['iva'] + facturacion['BEN2_62']['iva'] + facturacion['Otros']['iva']
        total_commission_subsidized = facturacion['BEN1_70']['commission'] + facturacion['BEN2_62']['commission'] + facturacion['Otros']['commission']

        return {
            'facturacion': facturacion,
            'subsidy_percentage_ben1': subsidy_percentage_ben1,
            'subsidy_percentage_ben2': subsidy_percentage_ben2,
            'total_subsidy': total_subsidy,
            'total_employee_payment': total_employee_payment,
            'total_iva': total_iva,
            'total_commission': total_commission,
            'total_commission_subsidized': total_commission_subsidized
        }

    def _calcular_comisiones_no_subsidiadas(self):
        df = self.datos
        comisiones = []
        total_commission_non_subsidized = 0
        for _, row in df.iterrows():
            if not row['is_subsidized']:
                commission = row['asoavna_commission'] * row['quantity']
                total_commission_non_subsidized += commission
                comisiones.append({
                    'client': row['client'],
                    'display_name': row['display_name'],
                    'product': row['product'],
                    'total': row['total'] * row['quantity'],
                    'base_price': row['base_price'] * row['quantity'],
                    'asoavna_commission': commission,
                    'iva': row['iva'] * row['quantity']
                })
        return pd.DataFrame(comisiones), total_commission_non_subsidized

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
        revenue_by_client = (filtered_df.groupby('display_name')
                            .agg({'total': 'sum', 'quantity': 'sum'})
                            .apply(lambda x: x['total'] * x['quantity'], axis=1)
                            .to_dict())

        sales_by_date_df = (filtered_df.groupby(filtered_df['date'].dt.strftime('%Y-%m-%d'))
                           .agg({'total': 'sum', 'quantity': 'sum'}))
        sales_by_date = (sales_by_date_df['total'] * sales_by_date_df['quantity']).to_dict()

        product_distribution = filtered_df.groupby('product')['quantity'].sum().to_dict()

        consumption_by_contact = filtered_df.groupby('display_name').apply(lambda x: x.to_dict('records')).to_dict()

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

# Formatear números con el símbolo de colones y dos decimales
def format_number(num):
    if not isinstance(num, (int, float)) or pd.isna(num):
        return '₡0.00'
    return f"₡{num:.2f}"

# Cargar datos usando variables de entorno
def load_data():
    try:
        sales_data_path = os.getenv('SALES_DATA_PATH', 'sales_data.csv')
        users_data_path = os.getenv('USERS_DATA_PATH', 'users_data.csv')
        sales_df = pd.read_csv(sales_data_path)
        user_df = pd.read_csv(users_data_path)
        return sales_df, user_df
    except Exception as e:
        st.error(f"Ocurrió un error al cargar los datos: {e}. Asegúrate de que los archivos estén disponibles y tengan el formato correcto.")
        return None, None

# Sistema de Login usando variables de entorno
def check_login(username, password):
    admin_username = os.getenv('ADMIN_USERNAME', 'admin')
    admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
    stored_users = {
        admin_username: hashlib.sha256(admin_password.encode()).hexdigest()
    }
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    return username in stored_users and stored_users[username] == hashed_password

# Main app
def main():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False

    st.title("Sistema de Reportes de Ventas - ASEAVNA")
    st.markdown("**Generado el 19 de abril de 2025 para C2-ASEAVNA, Grecia, Costa Rica**")
    st.markdown("Sistema profesional para la gestión de ventas, subsidios y comisiones.")

    tabs = st.tabs(["Login", "Facturación", "Gráficos", "Historial de Consumo", "Reporte Individual", "Comisiones No Subsidiadas"])

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
            col_logout, col_clear = st.columns(2)
            with col_logout:
                if st.button("Cerrar Sesión"):
                    st.session_state.logged_in = False
                    st.rerun()
            with col_clear:
                if st.button("Limpiar Estado de Sesión"):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.session_state.logged_in = True
                    st.success("Estado de sesión limpiado. Por favor, recarga la página.")
                    st.rerun()

    if not st.session_state.logged_in:
        return

    if 'loaded_data' not in st.session_state:
        sales_df, user_df = load_data()
        st.session_state.loaded_data = (sales_df, user_df)
    else:
        sales_df, user_df = st.session_state.loaded_data

    if sales_df is None or user_df is None:
        return

    # Configuración del IVA
    if 'iva_rate' not in st.session_state:
        st.session_state.iva_rate = 0  # Default 0%

    with tabs[0]:
        st.header("Configuración de IVA")
        iva_options = [0, 13]
        selected_iva = st.selectbox("Tasa de IVA (%)", iva_options, index=iva_options.index(st.session_state.iva_rate), key="iva_rate_select")
        if selected_iva != st.session_state.iva_rate:
            st.session_state.iva_rate = selected_iva
            st.session_state.reporte = None  # Forzar recálculo del reporte
            st.rerun()

    if 'reporte' not in st.session_state or st.session_state.reporte is None:
        try:
            st.session_state.reporte = ReporteVentas(sales_df, user_df, st.session_state.iva_rate)
        except Exception as e:
            st.error(f"Error al procesar los datos: {e}")
            return

    reporte = st.session_state.reporte
    sales_data = reporte.datos

    # Validar que sales_data tenga las columnas esperadas
    expected_columns = ['client', 'display_name', 'name', 'company', 'date', 'order', 'quantity', 'unit_price', 'total', 'base_price', 'product', 'seller', 'cedula', 'position', 'tipo', 'cost_center', 'is_subsidized', 'subsidy', 'employee_payment', 'employee_payment_base', 'asoavna_commission', 'iva', 'client_credit', 'aseavna_account']
    missing_columns = [col for col in expected_columns if col not in sales_data.columns]
    if missing_columns:
        st.error(f"Error: Faltan las siguientes columnas en los datos procesados: {', '.join(missing_columns)}. Verifica el formato de los archivos de datos.")
        return

    etiquetas_fila = reporte.etiquetas_fila
    facturacion = reporte.facturacion
    comisiones_no_subsidiadas_df, total_commission_non_subsidized = reporte.comisiones_no_subsidiadas
    reportes_individuales = reporte.reportes_individuales

    # Inicializar estados de filtros
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
    # Forzar la inicialización de sort_key y sort_direction
    st.session_state.sort_key = 'display_name'  # Forzar a 'display_name'
    st.session_state.sort_direction = 'asc'  # Forzar a 'asc'
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
            filtered_data['display_name'].str.lower().str.contains(st.session_state.search_query.lower(), na=False) |
            filtered_data['cedula'].str.lower().str.contains(st.session_state.search_query.lower(), na=False)
        ]
    if st.session_state.selected_cost_center != 'All':
        filtered_data = filtered_data[filtered_data['cost_center'] == st.session_state.selected_cost_center]
    if st.session_state.selected_client != 'All':
        filtered_data = filtered_data[filtered_data['client'] == st.session_state.selected_client]

    filtered_comisiones = comisiones_no_subsidiadas_df.copy()
    if st.session_state.selected_client != 'All':
        filtered_comisiones = filtered_comisiones[filtered_comisiones['client'] == st.session_state.selected_client]

    # Recalcular etiquetas_fila con datos filtrados
    filtered_etiquetas = reporte._generar_etiquetas_fila(filtered_data)

    # Depuración: Mostrar el valor de sort_key y las columnas de filtered_data
    st.write(f"Debug - sort_key: {st.session_state.sort_key}")
    st.write(f"Debug - filtered_data columns: {list(filtered_data.columns)}")
    st.write(f"Debug - filtered_data shape: {filtered_data.shape}")

    # Validar que sort_key sea una columna válida en filtered_data
    if st.session_state.sort_key not in filtered_data.columns:
        st.error(f"Error: La columna '{st.session_state.sort_key}' no existe en filtered_data. Columnas disponibles: {list(filtered_data.columns)}. Revirtiendo a 'display_name'.")
        st.session_state.sort_key = 'display_name'  # Revertir a una columna conocida
        if 'display_name' not in filtered_data.columns:
            st.error("Error crítico: La columna 'display_name' no está presente en filtered_data. Verifica los datos de entrada.")
            return

    filtered_data = filtered_data.sort_values(
        by=st.session_state.sort_key,
        ascending=(st.session_state.sort_direction == 'asc')
    )

    aggregated = reporte.aggregate_data(filtered_data)

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
    if len(sales_trend_data) > 100:
        sales_trend_data = sales_trend_data.iloc[::len(sales_trend_data)//100]

    product_pie_data = pd.DataFrame([
        {'name': k, 'value': v} for k, v in aggregated['product_distribution'].items()
    ])

    cost_breakdown_data = aggregated['cost_breakdown_by_tipo']

    # Filtros
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
            selected_client = st.selectbox("Cliente", unique_clients, index=unique_clients.index(st.session_state.selected_client) if st.session_state.selected_client in unique_clients else 0, key="client_filter")

        # Forzar actualización de los filtros
        if (selected_tipo != st.session_state.selected_tipo or
            start_date != st.session_state.date_range_start or
            end_date != st.session_state.date_range_end or
            search_query != st.session_state.search_query or
            selected_cost_center != st.session_state.selected_cost_center or
            selected_client != st.session_state.selected_client):
            st.session_state.selected_tipo = selected_tipo
            st.session_state.date_range_start = start_date
            st.session_state.date_range_end = end_date
            st.session_state.search_query = search_query
            st.session_state.selected_cost_center = selected_cost_center
            st.session_state.selected_client = selected_client
            st.session_state.current_page = 1
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
        st.write("Nota: Los subsidios y costos asociados se aplican únicamente al producto 'Almuerzo Ejecutivo Aseavna' para BEN1 y BEN2.")

        facturacion_filtered = {
            'BEN1_70': {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0},
            'BEN2_62': {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0},
            'Otros': {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0}
        }
        for _, row in filtered_data.iterrows():
            if not row['is_subsidized']:
                continue
            if row['tipo'] == 'BEN1_70':
                facturacion_filtered['BEN1_70']['count'] += row['quantity']
                facturacion_filtered['BEN1_70']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion_filtered['BEN1_70']['employee_payment'] += row['employee_payment_base'] * row['quantity']
                facturacion_filtered['BEN1_70']['iva'] += row['iva'] * row['quantity']
                facturacion_filtered['BEN1_70']['commission'] += row['asoavna_commission'] * row['quantity']
            elif row['tipo'] == 'BEN2_62':
                facturacion_filtered['BEN2_62']['count'] += row['quantity']
                facturacion_filtered['BEN2_62']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion_filtered['BEN2_62']['employee_payment'] += row['employee_payment_base'] * row['quantity']
                facturacion_filtered['BEN2_62']['iva'] += row['iva'] * row['quantity']
                facturacion_filtered['BEN2_62']['commission'] += row['asoavna_commission'] * row['quantity']
            else:
                facturacion_filtered['Otros']['count'] += row['quantity']
                facturacion_filtered['Otros']['subsidy'] += row['subsidy'] * row['quantity']
                facturacion_filtered['Otros']['employee_payment'] += row['employee_payment'] * row['quantity']
                facturacion_filtered['Otros']['iva'] += row['iva'] * row['quantity']
                facturacion_filtered['Otros']['commission'] += row['asoavna_commission'] * row['quantity']

        total_subsidy_filtered = facturacion_filtered['BEN1_70']['subsidy'] + facturacion_filtered['BEN2_62']['subsidy'] + facturacion_filtered['Otros']['subsidy']
        total_employee_payment_filtered = facturacion_filtered['BEN1_70']['employee_payment'] + facturacion_filtered['BEN2_62']['employee_payment'] + facturacion_filtered['Otros']['employee_payment']
        total_iva_filtered = facturacion_filtered['BEN1_70']['iva'] + facturacion_filtered['BEN2_62']['iva'] + facturacion_filtered['Otros']['iva']
        total_commission_subsidized_filtered = facturacion_filtered['BEN1_70']['commission'] + facturacion_filtered['BEN2_62']['commission'] + facturacion_filtered['Otros']['commission']

        # Calcular comisiones no subsidiadas para datos filtrados directamente
        comisiones = []
        total_commission_non_subsidized_filtered = 0
        for _, row in filtered_data.iterrows():
            if not row['is_subsidized']:
                commission = row['asoavna_commission'] * row['quantity']
                total_commission_non_subsidized_filtered += commission
                comisiones.append({
                    'client': row['client'],
                    'display_name': row['display_name'],
                    'product': row['product'],
                    'total': row['total'] * row['quantity'],
                    'base_price': row['base_price'] * row['quantity'],
                    'asoavna_commission': commission,
                    'iva': row['iva'] * row['quantity']
                })
        filtered_comisiones_df = pd.DataFrame(comisiones)
        non_subsidized_iva = filtered_comisiones_df['iva'].sum() if not filtered_comisiones_df.empty else 0

        # Calcular totales
        total_ben1_filtered = facturacion_filtered['BEN1_70']['subsidy'] + facturacion_filtered['BEN1_70']['employee_payment']
        total_ben2_filtered = facturacion_filtered['BEN2_62']['subsidy'] + facturacion_filtered['BEN2_62']['employee_payment']
        subsidy_percentage_ben1_filtered = (facturacion_filtered['BEN1_70']['subsidy'] / total_ben1_filtered * 100) if total_ben1_filtered > 0 else 0
        subsidy_percentage_ben2_filtered = (facturacion_filtered['BEN2_62']['subsidy'] / total_ben2_filtered * 100) if total_ben2_filtered > 0 else 0

        # Crear tabla de facturación
        facturacion_df = pd.DataFrame([
            {'': 'Subsidio', 'BEN1_70 Con 5% para ASOANVA': 2100 if facturacion_filtered['BEN1_70']['count'] > 0 else 0, 'BEN2_62 Con 5% para ASOANVA': 1800 if facturacion_filtered['BEN2_62']['count'] > 0 else 0},
            {'': 'Diferencia', 'BEN1_70 Con 5% para ASOANVA': 1000 if facturacion_filtered['BEN1_70']['count'] > 0 else 0, 'BEN2_62 Con 5% para ASOANVA': 1300 if facturacion_filtered['BEN2_62']['count'] > 0 else 0},
            {'': 'Iva', 'BEN1_70 Con 5% para ASOANVA': facturacion_filtered['BEN1_70']['iva'], 'BEN2_62 Con 5% para ASOANVA': facturacion_filtered['BEN2_62']['iva']},
            {'': 'PAGAR a colaborador', 'BEN1_70 Con 5% para ASOANVA': 1000 if facturacion_filtered['BEN1_70']['count'] > 0 else 0, 'BEN2_62 Con 5% para ASOANVA': 1300 if facturacion_filtered['BEN2_62']['count'] > 0 else 0},
            {'': 'Precio con iva', 'BEN1_70 Con 5% para ASOANVA': 3100 if facturacion_filtered['BEN1_70']['count'] > 0 else 0, 'BEN2_62 Con 5% para ASOANVA': 3100 if facturacion_filtered['BEN2_62']['count'] > 0 else 0},
            {'': '% Subsidio', 'BEN1_70 Con 5% para ASOANVA': f"{subsidy_percentage_ben1_filtered:.2f}%", 'BEN2_62 Con 5% para ASOANVA': f"{subsidy_percentage_ben2_filtered:.2f}%"},
            {'': 'Facturar a AVNA', 'BEN1_70 Con 5% para ASOANVA': facturacion_filtered['BEN1_70']['subsidy'], 'BEN2_62 Con 5% para ASOANVA': facturacion_filtered['BEN2_62']['subsidy']},
            {'': 'Monto a cobrar al trabajador', 'BEN1_70 Con 5% para ASOANVA': facturacion_filtered['BEN1_70']['employee_payment'], 'BEN2_62 Con 5% para ASOANVA': facturacion_filtered['BEN2_62']['employee_payment']},
            {'': 'Total', 'BEN1_70 Con 5% para ASOANVA': total_ben1_filtered, 'BEN2_62 Con 5% para ASOANVA': total_ben2_filtered},
            {'': 'Aseavna colones', 'BEN1_70 Con 5% para ASOANVA': 155 if facturacion_filtered['BEN1_70']['count'] > 0 else 0, 'BEN2_62 Con 5% para ASOANVA': 150 if facturacion_filtered['BEN2_62']['count'] > 0 else 0},
            {'': 'Aseavna %', 'BEN1_70 Con 5% para ASOANVA': '5,0%' if facturacion_filtered['BEN1_70']['count'] > 0 else '0,0%', 'BEN2_62 Con 5% para ASOANVA': '5,0%' if facturacion_filtered['BEN2_62']['count'] > 0 else '0,0%'},
        ])

        # Añadir columna Total
        facturacion_df['Total'] = facturacion_df.apply(lambda row: (row['BEN1_70 Con 5% para ASOANVA'] + row['BEN2_62 Con 5% para ASOANVA']) if isinstance(row['BEN1_70 Con 5% para ASOANVA'], (int, float)) else '', axis=1)

        # Añadir conteo de transacciones
        facturacion_df.loc[facturacion_df[''] == 'Total', 'BEN1_70 Con 5% para ASOANVA'] = facturacion_filtered['BEN1_70']['count']
        facturacion_df.loc[facturacion_df[''] == 'Total', 'BEN2_62 Con 5% para ASOANVA'] = facturacion_filtered['BEN2_62']['count']
        facturacion_df.loc[facturacion_df[''] == 'Total', 'Total'] = facturacion_filtered['BEN1_70']['count'] + facturacion_filtered['BEN2_62']['count']

        st.dataframe(facturacion_df, use_container_width=True)

        # Facturación adicional
        total_facturar_avna = total_subsidy_filtered
        total_aseavna_recoleta = total_employee_payment_filtered + filtered_comisiones_df['total'].sum() if not filtered_comisiones_df.empty else total_employee_payment_filtered
        total_aseavna_5percent = total_commission_non_subsidized_filtered
        total_facturar_aseavna = total_aseavna_recoleta - total_aseavna_5percent
        total_aseavna_5percent_subsidized = total_commission_subsidized_filtered
        total_facturar_aseavna_final = total_aseavna_recoleta - total_aseavna_5percent_subsidized

        facturacion_adicional_df = pd.DataFrame([
            {'': 'Facturar a AVNA y pagar Avna', 'BEN1_70': format_number(facturacion_filtered['BEN1_70']['subsidy']), 'BEN2_62': format_number(facturacion_filtered['BEN2_62']['subsidy']), 'Total': format_number(total_facturar_avna)},
            {'': 'Aseavna recoleta', 'BEN1_70': format_number(facturacion_filtered['BEN1_70']['employee_payment']), 'BEN2_62': format_number(facturacion_filtered['BEN2_62']['employee_payment']), 'Total': format_number(total_aseavna_recoleta)},
            {'': 'Aseavna 5%', 'BEN1_70': format_number(0), 'BEN2_62': format_number(0), 'Total': format_number(total_aseavna_5percent)},
            {'': 'Facturar a ASEAVNA y pagarASEAVNA', 'BEN1_70': format_number(facturacion_filtered['BEN1_70']['employee_payment']), 'BEN2_62': format_number(facturacion_filtered['BEN2_62']['employee_payment']), 'Total': format_number(total_facturar_aseavna)},
            {'': 'Aseavna 5%', 'BEN1_70': format_number(facturacion_filtered['BEN1_70']['commission']), 'BEN2_62': format_number(facturacion_filtered['BEN2_62']['commission']), 'Total': format_number(total_aseavna_5percent_subsidized)},
            {'': 'Facturar a ASEAVNA y pagarASEAVNA', 'BEN1_70': format_number(facturacion_filtered['BEN1_70']['employee_payment'] - facturacion_filtered['BEN1_70']['commission']), 'BEN2_62': format_number(facturacion_filtered['BEN2_62']['employee_payment'] - facturacion_filtered['BEN2_62']['commission']), 'Total': format_number(total_facturar_aseavna_final)},
        ])
        st.dataframe(facturacion_adicional_df, use_container_width=True)

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

    # Pestaña de Historial de Consumo (Etiquetas de la fila)
    with tabs[3]:
        st.header("Historial de Consumo por Contacto (Etiquetas de la fila)")
        display_etiquetas = filtered_etiquetas.copy()
        display_etiquetas['Suma de Monto Cliente'] = display_etiquetas['Suma de Monto Cliente'].apply(lambda x: format_number(x) if isinstance(x, (int, float)) else x)
        display_etiquetas['Suma de Monto Subsidiado'] = display_etiquetas['Suma de Monto Subsidiado'].apply(lambda x: format_number(x) if isinstance(x, (int, float)) else x)
        st.dataframe(display_etiquetas, use_container_width=True)

    # Pestaña de Reporte Individual
    with tabs[4]:
        st.header("Reporte Individual")
        if st.session_state.selected_client != 'All':
            client_data = reportes_individuales.get(st.session_state.selected_client, None)
            if client_data:
                client_display_name = client_data['transacciones']['display_name'].iloc[0]
                st.subheader(f"Reporte para: {client_display_name}")
                col_client = st.columns(2)
                with col_client[0]:
                    st.metric("Total en Cuenta de Crédito del Cliente", format_number(client_data['total_client_credit']))
                with col_client[1]:
                    st.metric("Total en Cuenta de Aseavna", format_number(client_data['total_aseavna_account']))

                st.subheader("Transacciones Subsidiadas (Almuerzo Ejecutivo Aseavna)")
                subsidized_df = client_data['subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account', 'iva']]
                subsidized_df['date'] = subsidized_df['date'].dt.strftime('%Y-%m-%d')
                subsidized_df['total'] = subsidized_df['total'].apply(format_number)
                subsidized_df['subsidy'] = subsidized_df['subsidy'].apply(format_number)
                subsidized_df['employee_payment'] = subsidized_df['employee_payment'].apply(format_number)
                subsidized_df['asoavna_commission'] = (subsidized_df['asoavna_commission'] * subsidized_df['quantity']).apply(format_number)
                subsidized_df['client_credit'] = subsidized_df['client_credit'].apply(format_number)
                subsidized_df['aseavna_account'] = subsidized_df['aseavna_account'].apply(format_number)
                subsidized_df['iva'] = (subsidized_df['iva'] * subsidized_df['quantity']).apply(format_number)
                st.dataframe(subsidized_df, use_container_width=True)

                st.subheader("Transacciones No Subsidiadas")
                non_subsidized_df = client_data['non_subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account', 'iva']]
                non_subsidized_df['date'] = non_subsidized_df['date'].dt.strftime('%Y-%m-%d')
                non_subsidized_df['total'] = non_subsidized_df['total'].apply(format_number)
                non_subsidized_df['subsidy'] = non_subsidized_df['subsidy'].apply(format_number)
                non_subsidized_df['employee_payment'] = non_subsidized_df['employee_payment'].apply(format_number)
                non_subsidized_df['asoavna_commission'] = (non_subsidized_df['asoavna_commission'] * non_subsidized_df['quantity']).apply(format_number)
                non_subsidized_df['client_credit'] = non_subsidized_df['client_credit'].apply(format_number)
                non_subsidized_df['aseavna_account'] = non_subsidized_df['aseavna_account'].apply(format_number)
                non_subsidized_df['iva'] = (non_subsidized_df['iva'] * non_subsidized_df['quantity']).apply(format_number)
                st.dataframe(non_subsidized_df, use_container_width=True)
            else:
                st.write("No se encontraron datos para el cliente seleccionado.")
        else:
            st.write("Selecciona un cliente para ver su reporte individual.")

    # Pestaña de Comisiones No Subsidiadas
    with tabs[5]:
        st.header("Comisiones de Productos No Subsidiados")
        st.write("Nota: La comisión para productos no subsidiados es del 5% por transacción.")
        if not filtered_comisiones.empty:
            comisiones_display = filtered_comisiones.copy()
            comisiones_display['total'] = comisiones_display['total'].apply(format_number)
            comisiones_display['base_price'] = comisiones_display['base_price'].apply(format_number)
            comisiones_display['asoavna_commission'] = comisiones_display['asoavna_commission'].apply(format_number)
            comisiones_display['iva'] = comisiones_display['iva'].apply(format_number)
            st.dataframe(comisiones_display, use_container_width=True)
        else:
            st.write("No hay transacciones de productos no subsidiados con los filtros actuales.")

    # Opciones de Exportación
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
                        ['Ingresos Totales', format_number(total_revenue)],
                        ['Subsidios Totales', format_number(total_subsidies)],
                        ['Transacción Promedio', format_number(average_transaction)],
                        ['Transacciones Totales', len(filtered_data)],
                        ['Clientes Únicos', filtered_data['display_name'].nunique()]
                    ], columns=['Métrica', 'Valor'])
                    summary_data.to_excel(writer, sheet_name='Resumen', index=False)

                    if st.session_state.export_options['consumption_table']:
                        export_etiquetas = filtered_etiquetas.copy()
                        export_etiquetas.to_excel(writer, sheet_name='Consumo', index=False)

                    if st.session_state.export_options['facturacion_table']:
                        facturacion_data = pd.concat([facturacion_df, facturacion_adicional_df], ignore_index=True)
                        facturacion_data.to_excel(writer, sheet_name='Facturación', index=False)

                    if st.session_state.export_options['individual_report']:
                        for client, datos in reportes_individuales.items():
                            client_df = datos['transacciones'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account', 'iva']]
                            client_df.to_excel(writer, sheet_name=f'Cliente_{client[:20]}', index=False)

                    if st.session_state.export_options['non_subsidized_commissions']:
                        comisiones_df = filtered_comisiones[['display_name', 'product', 'total', 'base_price', 'asoavna_commission', 'iva']]
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
                export_df = filtered_etiquetas.copy()
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
                    config = pdfkit.configuration(wkhtmltopdf=r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe")
                    pdfkit.from_string("Reporte de Ventas", "reporte_ventas.pdf", configuration=config)
                    with open("reporte_ventas.pdf", "rb") as f:
                        pdf_data = f.read()
                    st.download_button(
                        label="Descargar PDF",
                        data=pdf_data,
                        file_name=f"reporte_ventas_aseavna_{datetime.now().strftime('%Y-%m-%d')}.pdf",
                        mime="application/pdf"
                    )
                except Exception as e:
                    st.warning(f"No se pudo generar el PDF. Asegúrate de que pdfkit y wkhtmltopdf estén instalados correctamente. Error: {e}")

if __name__ == "__main__":
    main()