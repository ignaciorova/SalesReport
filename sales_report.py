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
import tempfile
import platform
import yaml
from dotenv import load_dotenv
from typing import Optional, Dict, List
from html import escape
import locale

# Configuración de la página
st.set_page_config(page_title="Sistema de Reportes de Ventas - ASEAVNA", layout="wide")

# Cargar variables de entorno
load_dotenv()
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD_HASH = hashlib.sha256(os.getenv('ADMIN_PASSWORD', 'admin123').encode()).hexdigest()
WKHTMLTOPDF_PATH = os.getenv('WKHTMLTOPDF_PATH', 'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe' if platform.system() == "Windows" else '/usr/bin/wkhtmltopdf')

# Cargar configuración desde YAML
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
IVA_RATES = config['iva_rates']
COST_CENTERS = config.get('cost_centers', {
    'BEN1_70': 'CostCenter_BEN1',
    'BEN2_62': 'CostCenter_BEN2',
    'Practicante': 'CostCenter_Practicante',
    'AVNA GB': 'CostCenter_AVNA_GB',
    'AVNA ONBOARDING': 'CostCenter_AVNA_ONBOARDING',
    'AVNA VISITAS': 'CostCenter_AVNA_VISITAS'
})
SUBSIDY_RULES = config.get('subsidy_rules', {
    'BEN1_70': {'subsidy': 2100, 'employee_payment': 1000, 'commission': 150},
    'BEN2_62': {'subsidy': 1800, 'employee_payment': 1300, 'commission': 150}
})

# Clase para manejar contactos
class Contacto:
    def __init__(self, nombre: Optional[str], cedula: Optional[str], puesto: Optional[str], tipo: Optional[str]):
        """Initialize a contact with sanitized data."""
        self.nombre = nombre.strip() if isinstance(nombre, str) and pd.notna(nombre) else "Desconocido"
        self.cedula = str(cedula) if pd.notna(cedula) else "Desconocido"
        self.puesto = puesto if pd.notna(puesto) else "No especificado"
        self.tipo = tipo if pd.notna(tipo) else "Desconocido"
        self.normalized_name = self._normalize_name(self.nombre)

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for consistent lookup with improved flexibility."""
        if not isinstance(name, str):
            return ""
        name = name.strip().lower()
        name = ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
        name = name.replace(' ', '').replace('-', '')
        return name

    def to_dict(self) -> Dict:
        """Convert contact to dictionary."""
        return {
            'name': self.nombre,
            'cedula': self.cedula,
            'position': self.puesto,
            'tipo': self.tipo,
            'normalized_name': self.normalized_name
        }

# Clase para manejar una venta
class Venta:
    def __init__(self, cliente: str, empresa: str, fecha: str, orden: str, cantidad: float,
                 precio_unitario: float, total: float, producto: str, vendedor: str, contacto: Optional[Contacto]):
        """Initialize a sale with client, product, and financial details."""
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
        self.tipo = client_parts[0].replace('ASEAVNA ', '') if len(client_parts) > 1 else "Desconocido"
        self.client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]
        self.contacto = contacto if contacto else Contacto(self.client_name, "Desconocido", "No especificado", self.tipo)
        self.name = self.contacto.nombre
        self.cedula = self.contacto.cedula
        self.position = self.contacto.puesto
        self.tipo = self.contacto.tipo if self.contacto.tipo != "Desconocido" else self.tipo
        self.cost_center = COST_CENTERS.get(self.tipo, 'CostCenter_Other')
        self.is_subsidized = (self.producto == 'Almuerzo Ejecutivo Aseavna')
        self.subsidy = 0
        self.employee_payment = self.total
        self.employee_payment_base = 0
        self.asoavna_commission = 0
        self.iva = 0
        self.client_credit = 0
        self.aseavna_account = 0
        self.base_price = 0

    def aplicar_subsidios_y_comisiones(self, iva_rate: float) -> None:
        """Apply subsidies and commissions based on client type and IVA rate."""
        # Calculate base price and IVA
        iva_factor = 1 + (iva_rate / 100) if iva_rate > 0 else 1
        self.base_price = self.total / iva_factor if iva_factor != 1 else self.total
        self.iva = self.total - self.base_price if iva_factor != 1 else 0

        if self.is_subsidized:
            # Use SUBSIDY_RULES to determine subsidy and employee payment
            rules = SUBSIDY_RULES.get(self.tipo, {})
            self.subsidy = rules.get('subsidy', 0) * self.cantidad
            self.employee_payment = rules.get('employee_payment', self.total) * self.cantidad
            self.asoavna_commission = rules.get('commission', 150) * self.cantidad

            # Recalculate total for subsidized products
            self.total = (self.subsidy + self.employee_payment) / self.cantidad
            self.base_price = self.total / iva_factor if iva_factor != 1 else self.total
            self.iva = (self.total - self.base_price) * self.cantidad if iva_factor != 1 else 0
            self.employee_payment_base = self.employee_payment / iva_factor if iva_factor != 1 else self.employee_payment
        else:
            self.subsidy = 0
            self.employee_payment = self.total
            self.employee_payment_base = self.base_price
            self.asoavna_commission = self.total * 0.05  # 5% commission for non-subsidized

        self.client_credit = self.employee_payment
        self.aseavna_account = self.subsidy

    def to_dict(self) -> Dict:
        """Convert sale to dictionary."""
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
    def __init__(self, sales_df: pd.DataFrame, user_df: pd.DataFrame, iva_rate: float):
        """Initialize sales report with data and IVA rate."""
        self.iva_rate = iva_rate
        self.contactos = self._procesar_contactos(user_df)
        self.ventas = self._procesar_ventas(sales_df)
        self._aplicar_subsidios_y_comisiones()
        self.datos = self._crear_dataframe()
        self.etiquetas_fila = self._generar_etiquetas_fila(self.datos)
        self.facturacion = self._calcular_facturacion()
        self.comisiones_no_subsidiadas = self._calcular_comisiones_no_subsidiadas()
        self.reportes_individuales = self._generar_reportes_individuales()

    def _procesar_contactos(self, user_df: pd.DataFrame) -> Dict[str, Contacto]:
        """Process user data into contact dictionary with improved matching."""
        if user_df.empty:
            return {}
        required_columns = ['Nombre', 'Cédula', 'Puesto', 'Tipo']
        missing_columns = [col for col in required_columns if col not in user_df.columns]
        if missing_columns:
            raise ValueError(f"Columnas faltantes en users_data.csv: {', '.join(missing_columns)}")

        user_df = user_df[user_df['Nombre'].notna() & (user_df['Nombre'].str.strip() != '')].copy()
        contactos = {}
        for _, row in user_df.iterrows():
            contacto = Contacto(row['Nombre'], row['Cédula'], row['Puesto'], row['Tipo'])
            contactos[contacto.normalized_name] = contacto
            # Add alternative normalizations for better matching
            alt_name = contacto.nombre.replace(' ', '').lower()
            if alt_name != contacto.normalized_name:
                contactos[alt_name] = contacto
        return contactos

    def _procesar_ventas(self, sales_df: pd.DataFrame) -> List[Venta]:
        """Process sales data into list of Venta objects."""
        if sales_df.empty:
            return []
        required_columns = ['Cliente', 'Empresa', 'Fecha de la orden', 'Orden', 'Cant. ordenada', 'Precio unitario', 'Total', 'Variante del producto', 'Vendedor']
        missing_columns = [col for col in required_columns if col not in sales_df.columns]
        if missing_columns:
            raise ValueError(f"Columnas faltantes en sales_data.csv: {', '.join(missing_columns)}")

        def create_venta(row):
            cliente = row['Cliente']
            client_parts = cliente.split(', ')
            client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]
            normalized_name = Contacto(client_name, None, None, None).normalized_name
            # Try alternative normalization for matching
            alt_normalized_name = client_name.replace(' ', '').lower()
            contacto = self.contactos.get(normalized_name) or self.contactos.get(alt_normalized_name)
            return Venta(
                cliente=cliente,
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

        return [
            venta for _, row in sales_df.iterrows()
            if pd.notna(row['Total']) and row['Total'] != 0
            and (venta := create_venta(row)).fecha
        ]

    def _aplicar_subsidios_y_comisiones(self) -> None:
        """Apply subsidies and commissions to all sales."""
        for venta in self.ventas:
            venta.aplicar_subsidios_y_comisiones(self.iva_rate)

    def _crear_dataframe(self) -> pd.DataFrame:
        """Create DataFrame from sales data."""
        datos = [venta.to_dict() for venta in self.ventas]
        if not datos:
            return pd.DataFrame(columns=['client', 'display_name', 'name', 'company', 'date', 'order', 'quantity', 'unit_price', 'total', 'base_price', 'product', 'seller', 'cedula', 'position', 'tipo', 'cost_center', 'is_subsidized', 'subsidy', 'employee_payment', 'employee_payment_base', 'asoavna_commission', 'iva', 'client_credit', 'aseavna_account'])
        df = pd.DataFrame(datos)
        df['key'] = df['order'] + '-' + df['client'] + '-' + df['product']
        return df.drop_duplicates(subset='key').drop(columns='key')

    def _generar_etiquetas_fila(self, df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """Generate row labels for consumption history."""
        if df is None:
            df = self.datos.copy()
        if df.empty:
            return pd.DataFrame(columns=['Empleado', 'Producto', 'Suma de Cant. ordenada', 'Suma de Monto Cliente', 'Suma de Monto Subsidiado'])

        df['client_credit'] = pd.to_numeric(df['client_credit'], errors='coerce').fillna(0)
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)
        df['aseavna_account'] = pd.to_numeric(df['aseavna_account'], errors='coerce').fillna(0)

        grouped = df.groupby(['client', 'display_name', 'tipo', 'product']).agg({
            'quantity': 'sum',
            'client_credit': 'sum',
            'aseavna_account': 'sum'
        }).reset_index()

        labels = []
        tipos = sorted(grouped['tipo'].unique())
        for tipo in tipos:
            labels.append({
                'Empleado': f"ASEAVNA {tipo}",
                'Producto': '',
                'Suma de Cant. ordenada': '',
                'Suma de Monto Cliente': '',
                'Suma de Monto Subsidiado': ''
            })
            tipo_data = grouped[grouped['tipo'] == tipo]
            for _, row in tipo_data.sort_values(['client', 'product']).iterrows():
                labels.append({
                    'Empleado': row['display_name'],
                    'Producto': row['product'],
                    'Suma de Cant. ordenada': row['quantity'],
                    'Suma de Monto Cliente': row['client_credit'] * row['quantity'],
                    'Suma de Monto Subsidiado': row['aseavna_account'] * row['quantity']
                })

        expected_tipos = sorted(list(COST_CENTERS.keys()))
        for tipo in expected_tipos:
            if tipo not in tipos:
                labels.append({
                    'Empleado': f"ASEAVNA {tipo}",
                    'Producto': '(en blanco)',
                    'Suma de Cant. ordenada': 0,
                    'Suma de Monto Cliente': 0,
                    'Suma de Monto Subsidiado': 0
                })

        total_quantity = grouped['quantity'].sum()
        total_client = (grouped['client_credit'] * grouped['quantity']).sum()
        total_subsidized = (grouped['aseavna_account'] * grouped['quantity']).sum()
        labels.append({
            'Empleado': 'Total general',
            'Producto': '',
            'Suma de Cant. ordenada': total_quantity if not pd.isna(total_quantity) else 0,
            'Suma de Monto Cliente': total_client if not pd.isna(total_client) else 0,
            'Suma de Monto Subsidiado': total_subsidized if not pd.isna(total_subsidized) else 0
        })

        return pd.DataFrame(labels)

    def _calcular_facturacion(self) -> Dict:
        """Calculate billing details for subsidized products."""
        df = self.datos
        if df.empty:
            facturacion = {tipo: {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0} for tipo in COST_CENTERS.keys()}
            facturacion['Otros'] = {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0}
            return {
                'facturacion': facturacion,
                'subsidy_percentage': {tipo: 0 for tipo in COST_CENTERS.keys()},
                'total_subsidy': 0,
                'total_employee_payment': 0,
                'total_iva': 0,
                'total_commission': 0,
                'total_commission_subsidized': 0
            }

        facturacion = {tipo: {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0} for tipo in COST_CENTERS.keys()}
        facturacion['Otros'] = {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0}

        total_commission = 0
        for _, row in df.iterrows():
            total_commission += row['asoavna_commission']
            if not row['is_subsidized']:
                continue
            key = row['tipo'] if row['tipo'] in COST_CENTERS else 'Otros'
            facturacion[key]['count'] += row['quantity']
            facturacion[key]['subsidy'] += row['subsidy']
            facturacion[key]['employee_payment'] += row['employee_payment_base']
            facturacion[key]['iva'] += row['iva']
            facturacion[key]['commission'] += row['asoavna_commission']

        subsidy_percentage = {}
        for tipo in COST_CENTERS.keys():
            total = facturacion[tipo]['subsidy'] + facturacion[tipo]['employee_payment']
            subsidy_percentage[tipo] = (facturacion[tipo]['subsidy'] / total * 100) if total > 0 else 0

        total_subsidy = sum(f['subsidy'] for f in facturacion.values())
        total_employee_payment = sum(f['employee_payment'] for f in facturacion.values())
        total_iva = sum(f['iva'] for f in facturacion.values())
        total_commission_subsidized = sum(f['commission'] for f in facturacion.values())

        return {
            'facturacion': facturacion,
            'subsidy_percentage': subsidy_percentage,
            'total_subsidy': total_subsidy,
            'total_employee_payment': total_employee_payment,
            'total_iva': total_iva,
            'total_commission': total_commission,
            'total_commission_subsidized': total_commission_subsidized
        }

    def _calcular_comisiones_no_subsidiadas(self) -> tuple[pd.DataFrame, float]:
        """Calculate commissions for non-subsidized products."""
        df = self.datos
        if df.empty:
            return pd.DataFrame(columns=['client', 'display_name', 'product', 'total', 'base_price', 'asoavna_commission', 'iva']), 0

        comisiones = []
        total_commission_non_subsidized = 0
        for _, row in df.iterrows():
            if not row['is_subsidized']:
                commission = row['asoavna_commission']
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

    def _generar_reportes_individuales(self) -> Dict:
        """Generate individual client reports."""
        df = self.datos
        if df.empty or 'client' not in df.columns:
            return {}
        
        reportes = {}
        for client, group in df.groupby('client'):
            total_client_credit = group['client_credit'].sum()
            total_aseavna_account = group['aseavna_account'].sum()
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

    def aggregate_data(self, filtered_df: pd.DataFrame) -> Dict:
        """Aggregate data for visualizations."""
        if filtered_df.empty:
            return {
                'revenue_by_client': {},
                'sales_by_date': {},
                'product_distribution': {},
                'consumption_by_contact': {},
                'cost_breakdown_by_tipo': pd.DataFrame(columns=['tipo', 'subsidy', 'employee_payment', 'count'])
            }

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

# Formatear números con el símbolo de colones
def format_number(num: float, currency: str = 'CRC') -> str:
    """Format number as Costa Rican colones with fallback if locale is unavailable."""
    if not isinstance(num, (int, float)) or pd.isna(num) or num is None:
        return '₡0.00'
    
    try:
        locale.setlocale(locale.LC_MONETARY, 'es_CR.UTF-8')
        return locale.currency(num, grouping=True, symbol=True)
    except (locale.Error, ValueError):
        formatted_num = f"{float(num):,.2f}" if isinstance(num, (int, float)) else "0.00"
        return f"₡{formatted_num}"

# Cargar datos
@st.cache_data
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load sales and user data from CSV files."""
    try:
        sales_df = pd.read_csv('sales_data.csv')
        user_df = pd.read_csv('users_data.csv')
        return sales_df, user_df
    except Exception as e:
        st.error(f"Ocurrió un error al cargar los datos: {e}. Asegúrate de que los archivos sales_data.csv y users_data.csv estén disponibles y tengan el formato correcto.")
        return None, None

# Sistema de Login
def check_login(username: str, password: str) -> bool:
    """Verify user credentials."""
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    return username == ADMIN_USERNAME and hashed_password == ADMIN_PASSWORD_HASH

# Cache ReporteVentas
@st.cache_resource
def initialize_reporte(sales_df: pd.DataFrame, user_df: pd.DataFrame, iva_rate: float) -> ReporteVentas:
    """Initialize ReporteVentas with cached data."""
    return ReporteVentas(sales_df, user_df, iva_rate)

# Generar contenido PDF
def generate_pdf_content(facturacion_df: pd.DataFrame, facturacion_adicional_df: pd.DataFrame, filtered_etiquetas: pd.DataFrame, filtered_data: pd.DataFrame, template: str, current_date: str) -> str:
    """Generate HTML content for PDF based on template."""
    css_styles = """
    <style>
        body { font-family: Arial, sans-serif; }
        h1 { color: #1F77B4; text-align: center; }
        h2 { color: #333; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .summary { margin: 20px 0; text-align: center; }
        .currency::before { content: "₡"; }
    </style>
    """
    html_content = f"""
    <html>
    <head>
        {css_styles}
    </head>
    <body>
        <h1>Sistema de Reportes de Ventas - ASEAVNA</h1>
        <p class='summary'>Generado el {escape(current_date)} para C2-ASEAVNA, Grecia, Costa Rica</p>
        <p class='summary'>Sistema profesional para la gestión de ventas, subsidios y comisiones.</p>
    """

    if template == 'Ventas':
        html_content += "<h2>Desglose de Facturación (solo Almuerzo Ejecutivo Aseavna)</h2>"
        html_content += facturacion_df.to_html(index=False, escape=True, classes='facturacion-table', formatters={
            col: lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else x for col in facturacion_df.columns if col != ''
        })
        html_content += "<h2>Facturación Adicional</h2>"
        html_content += facturacion_adicional_df.to_html(index=False, escape=True, classes='facturacion-adicional-table')
        html_content += "<h2>Historial de Consumo por Contacto</h2>"
        html_content += filtered_etiquetas.to_html(index=False, escape=True, classes='consumo-table')
    elif template == 'Consumo por Empleado':
        html_content += "<h2>Consumo por Empleado</h2>"
        consumo_por_empleado = filtered_data.groupby(['display_name']).agg({
            'quantity': 'sum',
            'total': 'sum',
            'subsidy': 'sum',
            'employee_payment': 'sum'
        }).reset_index()
        consumo_por_empleado['total'] = consumo_por_empleado['total'] * consumo_por_empleado['quantity']
        consumo_por_empleado['subsidy'] = consumo_por_empleado['subsidy'] * consumo_por_empleado['quantity']
        consumo_por_empleado['employee_payment'] = consumo_por_empleado['employee_payment'] * consumo_por_empleado['quantity']
        consumo_por_empleado.columns = ['Empleado', 'Cantidad', 'Monto Total', 'Subsidio', 'Pago Empleado']
        html_content += consumo_por_empleado.to_html(index=False, escape=True, classes='consumo-empleado-table', formatters={
            'Monto Total': lambda x: f"{x:,.2f}",
            'Subsidio': lambda x: f"{x:,.2f}",
            'Pago Empleado': lambda x: f"{x:,.2f}"
        })
    elif template == 'Consumo por Productos':
        html_content += "<h2>Consumo por Productos</h2>"
        consumo_por_producto = filtered_data.groupby(['product']).agg({
            'quantity': 'sum',
            'total': 'sum',
            'subsidy': 'sum',
            'employee_payment': 'sum'
        }).reset_index()
        consumo_por_producto['total'] = consumo_por_producto['total'] * consumo_por_producto['quantity']
        consumo_por_producto['subsidy'] = consumo_por_producto['subsidy'] * consumo_por_producto['quantity']
        consumo_por_producto['employee_payment'] = consumo_por_producto['employee_payment'] * consumo_por_producto['quantity']
        consumo_por_producto.columns = ['Producto', 'Cantidad', 'Monto Total', 'Subsidio', 'Pago Empleado']
        html_content += consumo_por_producto.to_html(index=False, escape=True, classes='consumo-producto-table', formatters={
            'Monto Total': lambda x: f"{x:,.2f}",
            'Subsidio': lambda x: f"{x:,.2f}",
            'Pago Empleado': lambda x: f"{x:,.2f}"
        })
    elif template == 'Consumo por Centro de Costos':
        html_content += "<h2>Consumo por Centro de Costos</h2>"
        consumo_por_centro = filtered_data.groupby(['cost_center']).agg({
            'quantity': 'sum',
            'total': 'sum',
            'subsidy': 'sum',
            'employee_payment': 'sum'
        }).reset_index()
        consumo_por_centro['total'] = consumo_por_centro['total'] * consumo_por_centro['quantity']
        consumo_por_centro['subsidy'] = consumo_por_centro['subsidy'] * consumo_por_centro['quantity']
        consumo_por_centro['employee_payment'] = consumo_por_centro['employee_payment'] * consumo_por_centro['quantity']
        consumo_por_centro.columns = ['Centro de Costos', 'Cantidad', 'Monto Total', 'Subsidio', 'Pago Empleado']
        html_content += consumo_por_centro.to_html(index=False, escape=True, classes='consumo-centro-table', formatters={
            'Monto Total': lambda x: f"{x:,.2f}",
            'Subsidio': lambda x: f"{x:,.2f}",
            'Pago Empleado': lambda x: f"{x:,.2f}"
        })

    html_content += "</body></html>"
    return html_content

# Filtros con claves únicas por pestaña
def display_filters(tab_id: str):
    """Display filters with unique widget keys based on tab_id."""
    st.header("Filtros de Reporte")
    with st.container():
        st.write(f"**Resumen de Filtros**: Tipo: {st.session_state.selected_tipo}, "
                 f"Rango de Fechas: {st.session_state.date_range_start} a {st.session_state.date_range_end}, "
                 f"Centro de Costos: {st.session_state.selected_cost_center}, "
                 f"Cliente: {st.session_state.selected_client}")
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            unique_tipos = ['All'] + sorted(st.session_state.sales_data['tipo'].unique()) if not st.session_state.sales_data.empty else ['All']
            selected_tipo = st.selectbox("Tipo", unique_tipos, index=unique_tipos.index(st.session_state.selected_tipo) if st.session_state.selected_tipo in unique_tipos else 0, key=f"tipo_filter_{tab_id}")
        with col2:
            start_date, end_date = st.date_input(
                "Rango de Fechas",
                [st.session_state.date_range_start, st.session_state.date_range_end],
                min_value=st.session_state.sales_data['date'].min().date() if not st.session_state.sales_data.empty else datetime.today().date(),
                max_value=st.session_state.sales_data['date'].max().date() if not st.session_state.sales_data.empty else datetime.today().date(),
                key=f"date_filter_{tab_id}"
            )
        with col3:
            search_query = st.text_input("Buscar Cliente o Cédula", value=st.session_state.search_query, key=f"search_filter_{tab_id}")
        with col4:
            unique_cost_centers = ['All'] + sorted(st.session_state.sales_data['cost_center'].unique()) if not st.session_state.sales_data.empty else ['All']
            selected_cost_center = st.selectbox("Centro de Costos", unique_cost_centers, index=unique_cost_centers.index(st.session_state.selected_cost_center) if st.session_state.selected_cost_center in unique_cost_centers else 0, key=f"cost_center_filter_{tab_id}")
        with col5:
            unique_clients = ['All'] + sorted(st.session_state.sales_data['client'].unique()) if not st.session_state.sales_data.empty else ['All']
            selected_client = st.selectbox("Cliente", unique_clients, index=unique_clients.index(st.session_state.selected_client) if st.session_state.selected_client in unique_clients else 0, key=f"client_filter_{tab_id}")

        if isinstance(start_date, (list, tuple)):
            start_date = start_date[0] if start_date else st.session_state.date_range_start
        if isinstance(end_date, (list, tuple)):
            end_date = end_date[0] if end_date else st.session_state.date_range_end

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

        if st.button("Restablecer Filtros", key=f"reset_filters_{tab_id}"):
            st.session_state.selected_tipo = 'All'
            st.session_state.date_range_start = st.session_state.sales_data['date'].min().date() if not st.session_state.sales_data.empty else datetime.today().date()
            st.session_state.date_range_end = st.session_state.sales_data['date'].max().date() if not st.session_state.sales_data.empty else datetime.today().date()
            st.session_state.search_query = ''
            st.session_state.selected_cost_center = 'All'
            st.session_state.selected_client = 'All'
            st.session_state.current_page = 1
            st.rerun()

# Main app
def main():
    """Main application function."""
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False

    st.title("Sistema de Reportes de Ventas - ASEAVNA")
    current_date = datetime.now().strftime('%d de %B de %Y')
    st.markdown(f"**Generado el {current_date} para C2-ASEAVNA, Grecia, Costa Rica**")
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

    if 'iva_rate' not in st.session_state:
        st.session_state.iva_rate = 0

    with tabs[0]:
        st.header("Configuración de IVA")
        selected_iva = st.selectbox("Tasa de IVA (%)", IVA_RATES, index=IVA_RATES.index(st.session_state.iva_rate), key="iva_rate_select")
        if selected_iva != st.session_state.iva_rate:
            st.session_state.iva_rate = selected_iva
            st.session_state.reporte = None
            st.rerun()

    if 'reporte' not in st.session_state or st.session_state.iva_rate != st.session_state.get('last_iva_rate'):
        with st.spinner("Procesando datos..."):
            st.session_state.reporte = initialize_reporte(sales_df, user_df, st.session_state.iva_rate)
            st.session_state.last_iva_rate = st.session_state.iva_rate
    reporte = st.session_state.reporte
    sales_data = reporte.datos
    st.session_state.sales_data = sales_data

    expected_columns = ['client', 'display_name', 'name', 'company', 'date', 'order', 'quantity', 'unit_price', 'total', 'base_price', 'product', 'seller', 'cedula', 'position', 'tipo', 'cost_center', 'is_subsidized', 'subsidy', 'employee_payment', 'employee_payment_base', 'asoavna_commission', 'iva', 'client_credit', 'aseavna_account']
    missing_columns = [col for col in expected_columns if col not in sales_data.columns]
    if missing_columns:
        st.error(f"Error: Faltan las siguientes columnas en los datos procesados: {', '.join(missing_columns)}. Verifica el formato de 'sales_data.csv' y 'users_data.csv'.")
        return

    etiquetas_fila = reporte.etiquetas_fila
    facturacion = reporte.facturacion
    comisiones_no_subsidiadas_df, total_commission_non_subsidized = reporte.comisiones_no_subsidiadas
    reportes_individuales = reporte.reportes_individuales

    if 'selected_tipo' not in st.session_state:
        st.session_state.selected_tipo = 'All'
    if 'date_range_start' not in st.session_state:
        st.session_state.date_range_start = sales_data['date'].min().date() if not sales_data.empty else datetime.today().date()
    if 'date_range_end' not in st.session_state:
        st.session_state.date_range_end = sales_data['date'].max().date() if not sales_data.empty else datetime.today().date()
    if 'search_query' not in st.session_state:
        st.session_state.search_query = ''
    if 'selected_cost_center' not in st.session_state:
        st.session_state.selected_cost_center = 'All'
    if 'selected_client' not in st.session_state:
        st.session_state.selected_client = 'All'
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 1
    if 'sort_key' not in st.session_state:
        st.session_state.sort_key = 'display_name'
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
    if 'pdf_template' not in st.session_state:
        st.session_state.pdf_template = 'Ventas'

    filtered_data = sales_data.copy()
    if st.session_state.selected_tipo != 'All':
        filtered_data = filtered_data[filtered_data['tipo'] == st.session_state.selected_tipo]
    if st.session_state.date_range_start and st.session_state.date_range_end:
        filtered_data = filtered_data[
            (filtered_data['date'].dt.date >= st.session_state.date_range_start) &
            (filtered_data['date'].dt.date <= st.session_state.date_range_end)
        ]
    if st.session_state.search_query:
        search_query = st.session_state.search_query.lower()
        filtered_data = filtered_data[
            filtered_data['display_name'].str.lower().str.contains(search_query, na=False) |
            filtered_data['cedula'].str.lower().str.contains(search_query, na=False)
        ]
    if st.session_state.selected_cost_center != 'All':
        filtered_data = filtered_data[filtered_data['cost_center'] == st.session_state.selected_cost_center]
    if st.session_state.selected_client != 'All':
        filtered_data = filtered_data[filtered_data['client'] == st.session_state.selected_client]

    filtered_comisiones = comisiones_no_subsidiadas_df.copy()
    if st.session_state.selected_client != 'All':
        filtered_comisiones = filtered_comisiones[filtered_comisiones['client'] == st.session_state.selected_client]

    filtered_etiquetas = reporte._generar_etiquetas_fila(filtered_data)
    filtered_data = filtered_data.sort_values(
        by=st.session_state.sort_key,
        ascending=(st.session_state.sort_direction == 'asc')
    )

    aggregated = reporte.aggregate_data(filtered_data)

    revenue_chart_data = pd.DataFrame([
        {'client': k, 'revenue': v} for k, v in aggregated['revenue_by_client'].items()
    ])
    total_revenue = revenue_chart_data['revenue'].sum() if not revenue_chart_data.empty else 0
    if not revenue_chart_data.empty:
        revenue_chart_data['percentage'] = (revenue_chart_data['revenue'] / total_revenue * 100).round(1) if total_revenue > 0 else 0
        revenue_chart_data['client'] = revenue_chart_data['client'].apply(
            lambda x: x[:17] + '...' if len(x) > 20 else x
        )

    sales_trend_data = pd.DataFrame([
        {'date': k, 'revenue': v} for k, v in aggregated['sales_by_date'].items()
    ])
    if not sales_trend_data.empty:
        sales_trend_data = sales_trend_data.sort_values('date')
        if len(sales_trend_data) > 100:
            sales_trend_data = sales_trend_data.iloc[::len(sales_trend_data)//100]
    else:
        sales_trend_data = pd.DataFrame(columns=['date', 'revenue'])

    product_pie_data = pd.DataFrame([
        {'name': k, 'value': v} for k, v in aggregated['product_distribution'].items()
    ])

    cost_breakdown_data = aggregated['cost_breakdown_by_tipo']

    with tabs[1]:
        with st.spinner("Procesando datos de facturación..."):
            display_filters("facturacion")
            st.header("Desglose de Facturación (solo Almuerzo Ejecutivo Aseavna)")
            st.write("Nota: Los subsidios y costos asociados se aplican únicamente al producto 'Almuerzo Ejecutivo Aseavna'.")

            facturacion_filtered = {tipo: {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0} for tipo in COST_CENTERS.keys()}
            facturacion_filtered['Otros'] = {'count': 0, 'subsidy': 0, 'employee_payment': 0, 'iva': 0, 'commission': 0}

            for _, row in filtered_data.iterrows():
                if not row['is_subsidized']:
                    continue
                key = row['tipo'] if row['tipo'] in COST_CENTERS else 'Otros'
                facturacion_filtered[key]['count'] += row['quantity']
                facturacion_filtered[key]['subsidy'] += row['subsidy']
                facturacion_filtered[key]['employee_payment'] += row['employee_payment_base']
                facturacion_filtered[key]['iva'] += row['iva']
                facturacion_filtered[key]['commission'] += row['asoavna_commission']

            total_subsidy_filtered = sum(f['subsidy'] for f in facturacion_filtered.values())
            total_employee_payment_filtered = sum(f['employee_payment'] for f in facturacion_filtered.values())
            total_iva_filtered = sum(f['iva'] for f in facturacion_filtered.values())
            total_commission_subsidized_filtered = sum(f['commission'] for f in facturacion_filtered.values())

            comisiones = []
            total_commission_non_subsidized_filtered = 0
            for _, row in filtered_data.iterrows():
                if not row['is_subsidized']:
                    commission = row['asoavna_commission']
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

            subsidy_percentage_filtered = {}
            for tipo in COST_CENTERS.keys():
                total = facturacion_filtered[tipo]['subsidy'] + facturacion_filtered[tipo]['employee_payment']
                subsidy_percentage_filtered[tipo] = (facturacion_filtered[tipo]['subsidy'] / total * 100) if total > 0 else 0

            columns = [''] + [f"{tipo} Con 5% para ASOANVA" for tipo in COST_CENTERS.keys() if tipo in ['BEN1_70', 'BEN2_62']] + ['Total']
            facturacion_df_data = [
                {'': 'Subsidio', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['subsidy'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Diferencia', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['employee_payment'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Iva', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['iva'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'PAGAR a colaborador', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['employee_payment'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Precio con iva', **{f"{tipo} Con 5% para ASOANVA": (facturacion_filtered[tipo]['subsidy'] + facturacion_filtered[tipo]['employee_payment']) / facturacion_filtered[tipo]['count'] if facturacion_filtered[tipo]['count'] > 0 else 0 for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': '% Subsidio', **{f"{tipo} Con 5% para ASOANVA": f"{subsidy_percentage_filtered[tipo]:.2f}%" for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Facturar a AVNA', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['subsidy'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Monto a cobrar al trabajador', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['employee_payment'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Total', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['count'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Aseavna colones', **{f"{tipo} Con 5% para ASOANVA": facturacion_filtered[tipo]['commission'] for tipo in ['BEN1_70', 'BEN2_62']}},
                {'': 'Aseavna %', **{f"{tipo} Con 5% para ASOANVA": '5,0%' if facturacion_filtered[tipo]['count'] > 0 else '0,0%' for tipo in ['BEN1_70', 'BEN2_62']}},
            ]

            for row in facturacion_df_data:
                row['Total'] = sum(row[f"{tipo} Con 5% para ASOANVA"] for tipo in ['BEN1_70', 'BEN2_62'] if isinstance(row[f"{tipo} Con 5% para ASOANVA"], (int, float))) if row[''] not in ['% Subsidio', 'Aseavna %'] else ''

            facturacion_df = pd.DataFrame(facturacion_df_data, columns=columns)
            st.dataframe(facturacion_df, use_container_width=True)

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

    with tabs[2]:
        with st.spinner("Generando gráficos..."):
            display_filters("graficos")
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

    with tabs[3]:
        display_filters("historial")
        st.header("Historial de Consumo por Contacto (Etiquetas de la fila)")
        display_etiquetas = filtered_etiquetas.copy()
        display_etiquetas['Suma de Monto Cliente'] = display_etiquetas['Suma de Monto Cliente'].apply(
            lambda x: format_number(x) if isinstance(x, (int, float)) and not pd.isna(x) else x
        )
        display_etiquetas['Suma de Monto Subsidiado'] = display_etiquetas['Suma de Monto Subsidiado'].apply(
            lambda x: format_number(x) if isinstance(x, (int, float)) and not pd.isna(x) else x
        )
        st.dataframe(display_etiquetas, use_container_width=True)

    with tabs[4]:
        display_filters("individual")
        st.header("Reporte Individual")
        if st.session_state.selected_client != 'All':
            client_data = reportes_individuales.get(st.session_state.selected_client, None)
            if client_data:
                client_display_name = client_data['transacciones']['display_name'].iloc[0] if not client_data['transacciones'].empty else "Desconocido"
                st.subheader(f"Reporte para: {client_display_name}")
                col_client = st.columns(2)
                with col_client[0]:
                    st.metric("Total en Cuenta de Crédito del Cliente", format_number(client_data['total_client_credit']))
                with col_client[1]:
                    st.metric("Total en Cuenta de Aseavna", format_number(client_data['total_aseavna_account']))

                st.subheader("Transacciones Subsidiadas (Almuerzo Ejecutivo Aseavna)")
                subsidized_df = client_data['subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account', 'iva']]
                if not subsidized_df.empty:
                    subsidized_df['date'] = subsidized_df['date'].dt.strftime('%Y-%m-%d')
                    subsidized_df['total'] = subsidized_df['total'].apply(format_number)
                    subsidized_df['subsidy'] = subsidized_df['subsidy'].apply(format_number)
                    subsidized_df['employee_payment'] = subsidized_df['employee_payment'].apply(format_number)
                    subsidized_df['asoavna_commission'] = subsidized_df['asoavna_commission'].apply(format_number)
                    subsidized_df['client_credit'] = subsidized_df['client_credit'].apply(format_number)
                    subsidized_df['aseavna_account'] = subsidized_df['aseavna_account'].apply(format_number)
                    subsidized_df['iva'] = subsidized_df['iva'].apply(format_number)
                    st.dataframe(subsidized_df, use_container_width=True)
                else:
                    st.write("No hay transacciones subsidiadas para este cliente.")

                st.subheader("Transacciones No Subsidiadas")
                non_subsidized_df = client_data['non_subsidized'][['date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'asoavna_commission', 'client_credit', 'aseavna_account', 'iva']]
                if not non_subsidized_df.empty:
                    non_subsidized_df['date'] = non_subsidized_df['date'].dt.strftime('%Y-%m-%d')
                    non_subsidized_df['total'] = non_subsidized_df['total'].apply(format_number)
                    non_subsidized_df['subsidy'] = non_subsidized_df['subsidy'].apply(format_number)
                    non_subsidized_df['employee_payment'] = non_subsidized_df['employee_payment'].apply(format_number)
                    non_subsidized_df['asoavna_commission'] = non_subsidized_df['asoavna_commission'].apply(format_number)
                    non_subsidized_df['client_credit'] = non_subsidized_df['client_credit'].apply(format_number)
                    non_subsidized_df['aseavna_account'] = non_subsidized_df['aseavna_account'].apply(format_number)
                    non_subsidized_df['iva'] = non_subsidized_df['iva'].apply(format_number)
                    st.dataframe(non_subsidized_df, use_container_width=True)
                else:
                    st.write("No hay transacciones no subsidiadas para este cliente.")
            else:
                st.write("No hay datos disponibles para el cliente seleccionado.")
        else:
            st.write("Selecciona un cliente para ver el reporte individual.")

    with tabs[5]:
        display_filters("comisiones")
        st.header("Comisiones No Subsidiadas")
        if not filtered_comisiones.empty:
            filtered_comisiones['total'] = filtered_comisiones['total'].apply(format_number)
            filtered_comisiones['base_price'] = filtered_comisiones['base_price'].apply(format_number)
            filtered_comisiones['asoavna_commission'] = filtered_comisiones['asoavna_commission'].apply(format_number)
            filtered_comisiones['iva'] = filtered_comisiones['iva'].apply(format_number)
            st.dataframe(filtered_comisiones, use_container_width=True)
            st.metric("Total Comisiones No Subsidiadas", format_number(total_commission_non_subsidized))
        else:
            st.write("No hay comisiones no subsidiadas para mostrar con los filtros actuales.")

    # Exportar a PDF
    st.sidebar.header("Exportar Reporte")
    pdf_template = st.sidebar.selectbox(
        "Seleccionar Plantilla de PDF",
        ['Ventas', 'Consumo por Empleado', 'Consumo por Productos', 'Consumo por Centro de Costos'],
        index=['Ventas', 'Consumo por Empleado', 'Consumo por Productos', 'Consumo por Centro de Costos'].index(st.session_state.pdf_template),
        key="pdf_template_select"
    )
    if pdf_template != st.session_state.pdf_template:
        st.session_state.pdf_template = pdf_template
        st.rerun()

    if st.sidebar.button("Generar PDF"):
        with st.spinner("Generando PDF..."):
            try:
                html_content = generate_pdf_content(facturacion_df, facturacion_adicional_df, filtered_etiquetas, filtered_data, st.session_state.pdf_template, current_date)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.html') as tmp_html:
                    tmp_html.write(html_content.encode('utf-8'))
                    tmp_html_path = tmp_html.name

                pdf_output = BytesIO()
                config = pdfkit.configuration(wkhtmltopdf=WKHTMLTOPDF_PATH)
                pdfkit.from_file(tmp_html_path, pdf_output, configuration=config)
                os.unlink(tmp_html_path)

                pdf_data = pdf_output.getvalue()
                b64_pdf = base64.b64encode(pdf_data).decode('utf-8')
                href = f'<a href="data:application/pdf;base64,{b64_pdf}" download="reporte_ventas.pdf">Descargar PDF</a>'
                st.sidebar.markdown(href, unsafe_allow_html=True)
                st.sidebar.success("PDF generado exitosamente.")
            except Exception as e:
                st.sidebar.error(f"Error al generar el PDF: {e}")

if __name__ == "__main__":
    main()