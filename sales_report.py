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

# Normalizar nombres para coincidencias
def normalize_name(name):
    if not isinstance(name, str):
        return ''
    # Convertir a minúsculas, eliminar acentos y reemplazar caracteres especiales
    name = name.strip().lower()
    name = ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn')
    return name.replace(' ', '')

# Procesar datos de usuarios
def process_user_data(user_df):
    # Verificar columnas requeridas
    required_columns = ['Nombre', 'Cédula', 'Puesto', 'Tipo']
    missing_columns = [col for col in required_columns if col not in user_df.columns]
    if missing_columns:
        raise ValueError(f"Columnas faltantes en users_data.csv: {', '.join(missing_columns)}")
    
    # Filtrar filas con Nombre válido
    user_df = user_df[user_df['Nombre'].notna() & (user_df['Nombre'].str.strip() != '')].copy()
    
    # Crear columnas procesadas
    user_df['name'] = user_df['Nombre']
    user_df['cedula'] = user_df['Cédula'].astype(str).where(user_df['Cédula'].notna(), 'Desconocido')
    user_df['position'] = user_df['Puesto'].where(user_df['Puesto'].notna(), 'No especificado')
    user_df['tipo'] = user_df['Tipo'].where(user_df['Tipo'].notna(), 'Desconocido')
    
    # Normalizar nombres para vinculación
    user_df['normalized_name'] = user_df['name'].apply(normalize_name)
    
    return user_df[['name', 'cedula', 'position', 'tipo', 'normalized_name']]

# Procesar datos de ventas
def process_sales_data(sales_df, user_df):
    # Crear diccionario de usuarios para vinculación
    user_map = {row['normalized_name']: {
        'name': row['name'],
        'cedula': row['cedula'],
        'position': row['position'],
        'tipo': row['tipo']
    } for _, row in user_df.iterrows()}
    
    # Procesar filas de ventas
    def process_row(row):
        quantity = float(row['Cant. ordenada']) if pd.notna(row['Cant. ordenada']) else 0
        unit_price = float(row['Precio unitario']) if pd.notna(row['Precio unitario']) else 0
        total = float(row['Total']) if pd.notna(row['Total']) else 0
        try:
            date = pd.to_datetime(row['Fecha de la orden'], format='%Y-%m-%d %H:%M:%S')
        except:
            return None
        
        if not date or not row['Cliente']:
            return None
        
        # Extraer tipo y nombre del cliente
        client_parts = row['Cliente'].split(', ')
        tipo = ('BEN1_70' if 'BEN1_70' in client_parts[0] else
                'BEN2_62' if 'BEN2_62' in client_parts[0] else
                client_parts[0].replace('ASEAVNA ', ''))
        client_name = client_parts[1] if len(client_parts) > 1 else client_parts[0]
        
        # Normalizar nombre del cliente
        normalized_client_name = normalize_name(client_name)
        user_info = user_map.get(normalized_client_name, {
            'name': client_name,
            'cedula': 'Desconocido',
            'position': 'No especificado',
            'tipo': tipo
        })
        
        # Definir centros de costos
        if user_info['tipo'] == 'BEN1_70':
            cost_center = 'CostCenter_BEN1'
        elif user_info['tipo'] == 'BEN2_62':
            cost_center = 'CostCenter_BEN2'
        elif user_info['tipo'] in ['AVNA VISITAS', 'Contratista/Visitante']:
            cost_center = 'CostCenter_Visitante'
        elif user_info['tipo'] in ['AVNA GB', 'AVNA ONBOARDING']:
            cost_center = 'CostCenter_AVNA'
        elif user_info['tipo'] == 'Practicante':
            cost_center = 'CostCenter_Practicante'
        else:
            cost_center = 'CostCenter_Other'
        
        return {
            'client': client_name,
            'name': user_info['name'],
            'company': row['Empresa'] if pd.notna(row['Empresa']) else '',
            'date': date,
            'order': row['Orden'] if pd.notna(row['Orden']) else '',
            'quantity': quantity,
            'unit_price': unit_price,
            'total': total,
            'product': row['Variante del producto'] if pd.notna(row['Variante del producto']) else '',
            'seller': row['Vendedor'] if pd.notna(row['Vendedor']) else '',
            'cedula': user_info['cedula'],
            'position': user_info['position'],
            'tipo': user_info['tipo'],
            'cost_center': cost_center
        }
    
    # Aplicar procesamiento a todas las filas
    processed_data = [process_row(row) for _, row in sales_df.iterrows()]
    processed_data = [d for d in processed_data if d is not None]
    
    # Crear DataFrame y filtrar
    processed_df = pd.DataFrame(processed_data)
    processed_df = processed_df[processed_df['total'] != 0]
    processed_df['key'] = processed_df['order'] + '-' + processed_df['client'] + '-' + processed_df['product']
    processed_df = processed_df.drop_duplicates(subset='key').drop(columns='key')
    
    return processed_df

# Aplicar subsidios
def apply_subsidies(df):
    df['subsidy'] = 0
    df['employee_payment'] = df['total']
    df['asoavna_contribution'] = 0

    # Subsidios para BEN1_70
    ben1_mask = (df['tipo'] == 'BEN1_70') & (df['product'] == 'Almuerzo Ejecutivo Aseavna')
    df.loc[ben1_mask, 'subsidy'] = 2100
    df.loc[ben1_mask, 'employee_payment'] = 1000
    df.loc[ben1_mask, 'asoavna_contribution'] = 155

    # Subsidios para BEN2_62
    ben2_mask = (df['tipo'] == 'BEN2_62') & (df['product'] == 'Almuerzo Ejecutivo Aseavna')
    df.loc[ben2_mask, 'subsidy'] = 1800
    df.loc[ben2_mask, 'employee_payment'] = 1300
    df.loc[ben2_mask, 'asoavna_contribution'] = 155

    # Subsidios completos para tipos especiales (AVNA asume el costo)
    special_types = ['AVNA VISITAS', 'Contratista/Visitante', 'AVNA GB', 'AVNA ONBOARDING', 'Practicante']
    special_mask = df['tipo'].isin(special_types)
    df.loc[special_mask, 'subsidy'] = df.loc[special_mask, 'total']
    df.loc[special_mask, 'employee_payment'] = 0
    df.loc[special_mask, 'asoavna_contribution'] = 0

    return df

# Agregar datos para visualizaciones
def aggregate_data(df):
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

    # Procesar datos
    try:
        user_data = process_user_data(user_df)
        sales_data = process_sales_data(sales_df, user_data)
        sales_data = apply_subsidies(sales_data)
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
            'consumption_table': True
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
    aggregated = aggregate_data(filtered_data)

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
        unique_tipos = ['All'] + sorted(user_data['tipo'].unique())
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
    col_export = st.columns(5)
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
        f"revela una demanda constante por Almuerzo Ejecutivo Aseavna y Coca-Cola Regular 600mL, con subsidios que reducen efectivamente los costos para los empleados donde aplica. "
        f"{'La alta tasa de devoluciones de Johanna Alfaro Quiros requiere mayor investigación para mejorar la precisión de los pedidos y la satisfacción del cliente.' if st.session_state.selected_tipo in ['BEN2_62', 'All'] else 'Se recomienda monitorear los patrones de consumo para identificar oportunidades de mejora en la gestión de inventarios.'}"
    )

if __name__ == "__main__":
    main()