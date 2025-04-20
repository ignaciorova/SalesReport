import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import pdfkit
import base64
from io import BytesIO
import unicodedata

# Configuración de la página
st.set_page_config(page_title="Informe de Análisis de Ventas", layout="wide")

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

        # Extraer tipo y nombre del cliente
        client_parts = self.cliente.split(', ')
        self.tipo = ('BEN1_70' if 'BEN1_70' in client_parts[0] else
                     'BEN2_62' if 'BEN2_62' in client_parts[0] else
                     client_parts[0].replace('ASEAVNA ', ''))
        self.client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]

        # Vincular con contacto
        self.contacto = contacto if contacto else Contacto(self.client_name, "Desconocido", "No especificado", self.tipo)
        self.name = self.contacto.nombre
        self.cedula = self.contacto.cedula
        self.position = self.contacto.puesto
        self.tipo = self.contacto.tipo if self.contacto.tipo != "Desconocido" else self.tipo

        # Asignar centro de costos
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

        # Inicializar costos
        self.subsidy = 0
        self.employee_payment = self.total
        self.asoavna_contribution = 0

    def aplicar_subsidios(self):
        # Solo aplicar subsidios si el producto es "Almuerzo Ejecutivo Aseavna"
        if self.producto == 'Almuerzo Ejecutivo Aseavna':
            if self.tipo == 'BEN1_70':
                self.subsidy = 2100  # 67.74% de 3100
                self.employee_payment = 1000
                self.asoavna_contribution = 155  # 5% de 3100
            elif self.tipo == 'BEN2_62':
                self.subsidy = 1800  # 56.00% de 3100
                self.employee_payment = 1300
                self.asoavna_contribution = 155  # 5% de 3100
            elif self.tipo in ['AVNA VISITAS', 'Contratista/Visitante', 'AVNA GB', 'AVNA ONBOARDING', 'Practicante']:
                self.subsidy = self.total
                self.employee_payment = 0
                self.asoavna_contribution = 0
        else:
            # Para otros productos, el empleado paga el total y no hay subsidio ni contribución
            self.subsidy = 0
            self.employee_payment = self.total
            self.asoavna_contribution = 0

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
            'subsidy': self.subsidy,
            'employee_payment': self.employee_payment,
            'asoavna_contribution': self.asoavna_contribution
        }

# Clase para manejar el reporte de ventas
class ReporteVentas:
    def __init__(self, sales_df, user_df):
        self.contactos = self._procesar_contactos(user_df)
        self.ventas = self._procesar_ventas(sales_df)
        self._aplicar_subsidios()
        self.datos = self._crear_dataframe()
        self.facturacion = self._calcular_facturacion()

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

    def _aplicar_subsidios(self):
        for venta in self.ventas:
            venta.aplicar_subsidios()

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
            if row['product'] != 'Almuerzo Ejecutivo Aseavna':
                continue
            if row['tipo'] == 'BEN1_70':
                facturacion['BEN1_70']['avna'] += row['subsidy']
                facturacion['BEN1_70']['aseavna'] += row['employee_payment']
                facturacion['BEN1_70']['count'] += 1
            elif row['tipo'] == 'BEN2_62':
                facturacion['BEN2_62']['avna'] += row['subsidy']
                facturacion['BEN2_62']['aseavna'] += row['employee_payment']
                facturacion['BEN2_62']['count'] += 1
            else:
                facturacion['Otros']['avna'] += row['subsidy']
                facturacion['Otros']['aseavna'] += row['employee_payment']
                facturacion['Otros']['count'] += 1

        total_transacciones_almuerzo = facturacion['BEN1_70']['count'] + facturacion['BEN2_62']['count']
        aseavna_contribution = total_transacciones_almuerzo * 155  # 5% por transacción de Almuerzo Ejecutivo Aseavna
        facturar_aseavna = (facturacion['BEN1_70']['aseavna'] + facturacion['BEN2_62']['aseavna'] +
                            facturacion['Otros']['aseavna'] + aseavna_contribution)

        return {
            'facturacion': facturacion,
            'aseavna_contribution': aseavna_contribution,
            'facturar_aseavna': facturar_aseavna
        }

    def aggregate_data(self):
        df = self.datos
        revenue_by_client = df.groupby('client')['total'].sum().to_dict()
        sales_by_date = df.groupby(df['date'].dt.strftime('%Y-%m-%d'))['total'].sum().to_dict()
        product_distribution = df.groupby('product')['quantity'].sum().to_dict()
        consumption_by_contact = df.groupby('client').apply(lambda x: x.to_dict('records')).to_dict()
        cost_breakdown_by_tipo = df.groupby('tipo')[['subsidy', 'employee_payment']].sum().reset_index()
        cost_breakdown_by_tipo['count'] = df.groupby('tipo').size()
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

# Main app
def main():
    # Cargar datos
    sales_df, user_df = load_data()
    if sales_df is None or user_df is None:
        return

    # Procesar datos con clases
    try:
        reporte = ReporteVentas(sales_df, user_df)
        sales_data = reporte.datos
        facturacion = reporte.facturacion
    except Exception as e:
        st.error(f"Error al procesar los datos: {e}")
        return

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
            'facturacion_table': True
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

    # Ordenar datos
    filtered_data = filtered_data.sort_values(
        by=st.session_state.sort_key,
        ascending=(st.session_state.sort_direction == 'asc')
    )

    # Agregar datos
    aggregated = reporte.aggregate_data()

    # Preparar datos para gráficos
    revenue_chart_data = pd.DataFrame([
        {'client': k, 'revenue': v} for k, v in aggregated['revenue_by_client'].items()
    ])
    total_revenue = revenue_chart_data['revenue'].sum()
    revenue_chart_data['percentage'] = (revenue_chart_data['revenue'] / total_revenue * 100).round(1)
    revenue_chart_data['client'] = revenue_chart_data['client'].apply(
        lambda x: x[:17] + '...' if len(x) > 20 else x
    )

    sales_trend_data = pd.DataFrame([
        {'date': k, 'revenue': v} for k, v in aggregated['sales_by_date'].items()
    ]).sort_values('date')

    product_pie_data = pd.DataFrame([
        {'name': k, 'value': v} for k, v in aggregated['product_distribution'].items()
    ])

    cost_breakdown_data = aggregated['cost_breakdown_by_tipo']

    # Título y descripción
    st.title("Informe de Análisis de Ventas")
    st.markdown("Generado el 19 de abril de 2025 para C2-ASEAVNA, Grecia, Costa Rica")

    # Filtros
    st.header("Filtros")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        unique_tipos = ['All'] + sorted(sales_data['tipo'].unique())
        st.session_state.selected_tipo = st.selectbox("Tipo", unique_tipos, index=unique_tipos.index(st.session_state.selected_tipo))
    with col2:
        start_date, end_date = st.date_input(
            "Rango de Fechas",
            [st.session_state.date_range_start, st.session_state.date_range_end],
            min_value=sales_data['date'].min().date(),
            max_value=sales_data['date'].max().date()
        )
        st.session_state.date_range_start = start_date
        st.session_state.date_range_end = end_date
    with col3:
        st.session_state.search_query = st.text_input("Buscar Cliente o Cédula", value=st.session_state.search_query)
    with col4:
        unique_cost_centers = ['All'] + sorted(sales_data['cost_center'].unique())
        st.session_state.selected_cost_center = st.selectbox("Centro de Costos", unique_cost_centers, index=unique_cost_centers.index(st.session_state.selected_cost_center))

    if st.button("Restablecer Filtros"):
        st.session_state.selected_tipo = 'All'
        st.session_state.date_range_start = sales_data['date'].min().date()
        st.session_state.date_range_end = sales_data['date'].max().date()
        st.session_state.search_query = ''
        st.session_state.selected_cost_center = 'All'
        st.rerun()

    # Opciones de Exportación
    st.header("Opciones de Exportación")
    col_export = st.columns(6)
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

    col_btn = st.columns(3)
    with col_btn[0]:
        if st.button("Exportar a Excel"):
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                total_revenue = filtered_data['total'].sum()
                total_subsidies = filtered_data['subsidy'].sum()
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
                    export_df = filtered_data[['client', 'name', 'cedula', 'position', 'tipo', 'date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center']]
                    export_df.to_excel(writer, sheet_name='Consumo', index=False)
                if st.session_state.export_options['facturacion_table']:
                    facturacion_data = pd.DataFrame([
                        ['Facturar a AVNA (BEN1_70)', f"₡{format_number(facturacion['facturacion']['BEN1_70']['avna'])}", facturacion['facturacion']['BEN1_70']['count']],
                        ['Pagar a Aseavna (BEN1_70)', f"₡{format_number(facturacion['facturacion']['BEN1_70']['aseavna'])}", facturacion['facturacion']['BEN1_70']['count']],
                        ['Facturar a AVNA (BEN2_62)', f"₡{format_number(facturacion['facturacion']['BEN2_62']['avna'])}", facturacion['facturacion']['BEN2_62']['count']],
                        ['Pagar a Aseavna (BEN2_62)', f"₡{format_number(facturacion['facturacion']['BEN2_62']['aseavna'])}", facturacion['facturacion']['BEN2_62']['count']],
                        ['Facturar a AVNA (Otros)', f"₡{format_number(facturacion['facturacion']['Otros']['avna'])}", facturacion['facturacion']['Otros']['count']],
                        ['Pagar a Aseavna (Otros)', f"₡{format_number(facturacion['facturacion']['Otros']['aseavna'])}", facturacion['facturacion']['Otros']['count']],
                        ['Contribución Aseavna (5%)', f"₡{format_number(facturacion['aseavna_contribution'])}", ''],
                        ['Total a Facturar a Aseavna', f"₡{format_number(facturacion['facturar_aseavna'])}", '']
                    ], columns=['Concepto', 'Monto', 'Transacciones'])
                    facturacion_data.to_excel(writer, sheet_name='Facturación', index=False)
            buffer.seek(0)
            st.download_button(
                label="Descargar Excel",
                data=buffer,
                file_name=f"informe_ventas_{st.session_state.selected_tipo}_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    with col_btn[1]:
        if st.button("Exportar a CSV"):
            export_df = filtered_data[['client', 'name', 'cedula', 'position', 'tipo', 'date', 'product', 'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center']]
            csv = export_df.to_csv(index=False)
            st.download_button(
                label="Descargar CSV",
                data=csv,
                file_name=f"informe_ventas_{st.session_state.selected_tipo}_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="text/csv"
            )

    with col_btn[2]:
        if st.button("Exportar a PDF"):
            st.warning("La exportación a PDF requiere la instalación de pdfkit y wkhtmltopdf. Por favor, configura tu entorno para habilitar esta funcionalidad.")

    # Resumen
    st.header("Resumen")
    st.write(f"Ingresos Totales: ₡{format_number(filtered_data['total'].sum())}")
    st.write(f"Subsidios Totales: ₡{format_number(filtered_data['subsidy'].sum())}")
    st.write(f"Transacciones Totales: {len(filtered_data)}")
    st.write(f"Clientes Únicos: {filtered_data['client'].nunique()}")
    st.markdown(
        f"Dato Interesante: {'Johanna Alfaro Quiros (BEN2_62) tiene una alta tasa de devoluciones, lo que sugiere problemas potenciales con la precisión o satisfacción de los pedidos.' if st.session_state.selected_tipo in ['BEN2_62', 'All'] else 'No se observaron patrones de devolución notables para este grupo.'}"
    )

    # Desglose de Facturación
    st.header("Desglose de Facturación (solo Almuerzo Ejecutivo Aseavna)")
    st.write("Nota: Los subsidios y costos asociados se aplican únicamente al producto 'Almuerzo Ejecutivo Aseavna'.")
    st.write(f"Facturar a AVNA (BEN1_70): ₡{format_number(facturacion['facturacion']['BEN1_70']['avna'])} ({facturacion['facturacion']['BEN1_70']['count']} transacciones)")
    st.write(f"Pagar a Aseavna (BEN1_70): ₡{format_number(facturacion['facturacion']['BEN1_70']['aseavna'])}")
    st.write(f"Facturar a AVNA (BEN2_62): ₡{format_number(facturacion['facturacion']['BEN2_62']['avna'])} ({facturacion['facturacion']['BEN2_62']['count']} transacciones)")
    st.write(f"Pagar a Aseavna (BEN2_62): ₡{format_number(facturacion['facturacion']['BEN2_62']['aseavna'])}")
    st.write(f"Facturar a AVNA (Otros): ₡{format_number(facturacion['facturacion']['Otros']['avna'])} ({facturacion['facturacion']['Otros']['count']} transacciones)")
    st.write(f"Pagar a Aseavna (Otros): ₡{format_number(facturacion['facturacion']['Otros']['aseavna'])}")
    st.write(f"Contribución Aseavna (5%): ₡{format_number(facturacion['aseavna_contribution'])}")
    st.write(f"Total a Facturar a Aseavna: ₡{format_number(facturacion['facturar_aseavna'])}")

    # Gráficos
    if st.session_state.export_options['revenue_chart']:
        st.header("Ingresos por Cliente")
        fig = px.bar(revenue_chart_data, x='client', y='revenue', text='percentage',
                     labels={'revenue': 'Ingresos (₡)', 'client': 'Cliente', 'percentage': 'Porcentaje (%)'},
                     color_discrete_sequence=['#1F77B4'])
        fig.update_traces(texttemplate='%{text}%', textposition='outside')
        fig.update_layout(yaxis_tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)

    if st.session_state.export_options['sales_trend']:
        st.header("Tendencia de Ventas Diarias")
        fig = px.line(sales_trend_data, x='date', y='revenue',
                      labels={'revenue': 'Ingresos (₡)', 'date': 'Fecha'},
                      color_discrete_sequence=['#FF7F0E'])
        fig.update_layout(yaxis_tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)

    if st.session_state.export_options['product_pie']:
        st.header("Distribución de Productos")
        fig = px.pie(product_pie_data, names='name', values='value',
                     color_discrete_sequence=['#2CA02C', '#D62728', '#9467BD'])
        st.plotly_chart(fig, use_container_width=True)

    if st.session_state.export_options['cost_breakdown']:
        st.header("Desglose de Costos por Tipo")
        fig = go.Figure(data=[
            go.Bar(name='Subsidio', x=cost_breakdown_data['tipo'], y=cost_breakdown_data['subsidy'], marker_color='#1F77B4'),
            go.Bar(name='Pago Empleado', x=cost_breakdown_data['tipo'], y=cost_breakdown_data['employee_payment'], marker_color='#FF7F0E')
        ])
        fig.update_layout(barmode='stack', yaxis_title='Monto (₡)', xaxis_title='Tipo', yaxis_tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)

    # Tabla de Consumo
    if st.session_state.export_options['consumption_table']:
        st.header("Historial de Consumo por Contacto")
        rows_per_page = 50
        total_pages = (len(filtered_data) + rows_per_page - 1) // rows_per_page
        st.session_state.current_page = max(1, min(st.session_state.current_page, total_pages))

        start_idx = (st.session_state.current_page - 1) * rows_per_page
        end_idx = start_idx + rows_per_page
        paginated_data = filtered_data.iloc[start_idx:end_idx].copy()
        paginated_data['date'] = paginated_data['date'].dt.strftime('%Y-%m-%d')
        paginated_data['total'] = paginated_data['total'].apply(format_number)
        paginated_data['subsidy'] = paginated_data['subsidy'].apply(format_number)
        paginated_data['employee_payment'] = paginated_data['employee_payment'].apply(format_number)

        # Agregar interacción para ordenar
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
            'Centro de Costos': 'cost_center'
        }
        col_sort = st.columns(2)
        with col_sort[0]:
            sort_by = st.selectbox("Ordenar por", list(sort_options.keys()))
            st.session_state.sort_key = sort_options[sort_by]
        with col_sort[1]:
            direction = st.selectbox("Dirección", ['Ascendente', 'Descendente'])
            st.session_state.sort_direction = 'asc' if direction == 'Ascendente' else 'desc'
            if sort_by or direction:
                st.rerun()

        # Mostrar la tabla con los campos relevantes
        st.dataframe(paginated_data[[
            'client', 'name', 'cedula', 'position', 'tipo', 'date', 'product',
            'quantity', 'total', 'subsidy', 'employee_payment', 'cost_center'
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

    # Conclusión
    st.header("Conclusión")
    st.write(
        f"El análisis de ventas para {'todos los grupos' if st.session_state.selected_tipo == 'All' else st.session_state.selected_tipo} "
        f"revela una demanda constante por Almuerzo Ejecutivo Aseavna y Coca-Cola Regular 600mL, con subsidios que reducen efectivamente los costos para los empleados solo en Almuerzo Ejecutivo Aseavna. "
        f"{'La alta tasa de devoluciones de Johanna Alfaro Quiros requiere mayor investigación para mejorar la precisión de los pedidos y la satisfacción del cliente.' if st.session_state.selected_tipo in ['BEN2_62', 'All'] else 'Se recomienda monitorear los patrones de consumo para identificar oportunidades de mejora en la gestión de inventarios.'}"
    )

if __name__ == "__main__":
    main()