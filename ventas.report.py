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