# Propuesta de mejoras: Control de impresiones 3D en CRM Extended

Este documento describe las mejoras del módulo **CRM Extended** para llevar el control de impresiones 3D: termómetro visual por estado, proceso de manufactura (diseño → entrega), control de gastos e ingresos, y comunicaciones por WhatsApp.

---

## 1. Termómetro visual por estado (Kanban)

### Objetivo
Ver en el Kanban de oportunidades el “nivel de avance” como un termómetro (barra de progreso o porcentaje), de modo que cada tarjeta refleje en qué punto del pipeline está la oportunidad.

### Cómo funciona
- Cada **etapa** del pipeline tiene un **peso** (secuencia o número de orden). Por ejemplo: Solicitud=0%, Diseño=10%, Propuesta enviada=25%, … Entregado/cerrado=100%.
- Se calcula un campo computado **`stage_progress`** (0–100) según la etapa actual.
- En la vista Kanban se muestra:
  - La **barra de colores** que ya tienes (por etapa).
  - Opcional: un **widget de progreso** (barra o badge con el porcentaje).

### Uso
1. En **CRM → Oportunidades**, vista **Kanban**.
2. Cada tarjeta mantiene el color por etapa (termómetro de colores).
3. Si se añade el widget de progreso, verás el porcentaje numérico (ej. “75%”) para comparar avances de un vistazo.

### Archivos tocados
- **Modelo**: campo `stage_progress` (computed) y mapeo etapa → porcentaje en `crm_extended_3D.py`.
- **Vista**: `crm_extended_view.xml` (Kanban) para mostrar `stage_progress` si se desea barra/badge.

---

## 2. Proceso de manufactura (diseño → entrega)

### Objetivo
Tener un “flujo de manufactura” ligado a cada oportunidad que refleje los pasos reales: Diseño, Cola de impresión, Imprimiendo, Postprocesado, Control de calidad, Listo para entregar, Entregado.

### Cómo funciona
- Nuevo modelo **`crm.print.process.step`** (pasos del proceso de impresión):
  - Vinculado a `crm.lead`.
  - Campos: nombre del paso, estado (pendiente/en curso/completado), fecha inicio/fin, responsable, notas.
- Los **pasos estándar** se crean desde datos XML (o por defecto al crear la oportunidad) según las etapas de tu pipeline (diseño, impresión, postproceso, QC, entrega).
- En el formulario de la oportunidad, una pestaña **“Proceso de impresión”** muestra la lista de pasos y permite marcar fechas y estado.

### Uso
1. Al trabajar una oportunidad, abres la pestaña **Proceso de impresión**.
2. Ves la lista de pasos (Diseño, Impresión, Postproceso, etc.).
3. Marcas cada paso como **En curso** o **Completado** y registras fechas y notas.
4. Así tienes trazabilidad desde diseño hasta entrega sin depender (por ahora) del módulo Manufactura (MRP). Más adelante se puede conectar con MRP si lo instalas.

### Archivos
- **Modelo**: `crm.print.process.step` en un nuevo archivo o en `crm_extended_3D.py`.
- **Datos**: definición de pasos por defecto en `data/` (opcional).
- **Vista**: pestaña en el formulario de `crm.lead` con tree de pasos.

---

## 3. Control de gastos e ingresos

### Objetivo
Saber cuánto cuesta cada oportunidad (material, tiempo, externalizado) y cuánto ingresa, para ver margen por oportunidad y por periodo.

### Cómo funciona
- En **`crm.lead`**:
  - **Ingresos**: se usa `expected_revenue` o `final_price` (ya existente) como ingreso previsto/real.
  - **Costes**: nuevo campo **`total_cost`** (suma de líneas de coste) o, en una versión simple, un único campo **`total_cost`** editable.
  - **Margen**: campo computado **`margin_amount`** = ingresos − costes, y opcional **`margin_percent`**.
- Opcional: modelo **`crm.lead.cost.line`** (líneas de coste por oportunidad) con: concepto, importe, tipo (material, mano de obra, otro), fecha. Así el **`total_cost`** es la suma de las líneas.

### Uso
1. En la ficha de la oportunidad, pestaña **Finanzas** (o bloque en Conditions):
   - Ver/editar **Precio final** (ingreso).
   - Ver/editar **Coste total** o gestionar **Líneas de coste**.
   - Ver **Margen** (importe y %).
2. En listados o informes puedes agrupar por etapa o por fecha para ver rentabilidad del pipeline.

### Archivos
- **Modelo**: campos en `crm_extended_3D.py`; opcional `crm_lead_cost_line.py` (o en el mismo archivo).
- **Vista**: sección o pestaña “Finanzas” en el formulario de la oportunidad.

---

## 4. Comunicaciones WhatsApp (seguimiento y posibles clientes)

### Objetivo
Registrar con qué contactos se ha hablado por WhatsApp y el seguimiento de las oportunidades (mensajes enviados, fechas, tipo de contacto).

### Cómo funciona
- **Teléfono para WhatsApp**: en el lead/contacto se usa el teléfono del `partner_id` o un campo opcional **`whatsapp_phone`** (por si el número de contacto es distinto al del partner).
- Nuevo modelo **`crm.whatsapp.communication`** (o `crm.communication`):
  - Relación con `crm.lead`.
  - Campos: fecha, dirección (enviado / recibido), tipo (seguimiento, cotización, recordatorio, otro), resumen del mensaje, usuario que registra.
- Botón **“Abrir WhatsApp”** en la ficha del lead: construye el enlace `https://wa.me/<número>` para abrir WhatsApp Web/App con ese número (sin enviar mensajes desde Odoo; solo registro y enlace).

### Uso
1. En la oportunidad, pestaña **Comunicaciones**:
   - Añades registros de tipo “Seguimiento”, “Cotización”, etc., con fecha y resumen.
   - Así queda historial de comunicaciones sin depender de una API de WhatsApp.
2. Botón **“Abrir en WhatsApp”**: abre WhatsApp con el número del contacto para que el usuario envíe el mensaje manualmente; luego puede volver a Odoo y registrar la comunicación en la pestaña.

### Nota
- No se implementa envío automático por API (requiere cuenta negocio y aprobación). Solo registro + enlace para uso manual.
- Si más adelante integras una API (Twilio, WhatsApp Business, etc.), el mismo modelo puede guardar el resultado del envío.

### Archivos
- **Modelo**: `crm.whatsapp.communication` (o `crm.communication`) y, si aplica, campo `whatsapp_phone` en el lead.
- **Vista**: pestaña “Comunicaciones” en el formulario; botón “Abrir en WhatsApp” en el header o en la pestaña.

---

## Resumen de nuevos elementos técnicos

| Elemento              | Tipo        | Descripción breve                                              |
|-----------------------|------------|----------------------------------------------------------------|
| `stage_progress`      | Campo (lead)| Porcentaje 0–100 según etapa (termómetro numérico).          |
| Termómetro Kanban     | Vista      | Uso de `stage_progress` y/o color por etapa.                  |
| `crm.print.process.step` | Modelo   | Pasos del proceso diseño → entrega.                          |
| Proceso impresión     | Vista      | Pestaña con pasos en el formulario del lead.                 |
| `total_cost` / margen | Campos (lead) | Coste total y margen (ingresos − costes).                |
| Líneas de coste       | Modelo (opcional) | Líneas de coste por oportunidad.                        |
| Finanzas              | Vista      | Bloque/pestaña con ingresos, costes y margen.                |
| `whatsapp_phone`      | Campo (lead)| Teléfono para enlace WhatsApp (opcional si usas partner).      |
| `crm.whatsapp.communication` | Modelo | Historial de comunicaciones WhatsApp.                  |
| Comunicaciones        | Vista      | Pestaña + botón “Abrir en WhatsApp”.                         |

---

## Orden de implementación sugerido

1. **Termómetro**: `stage_progress` + ajuste Kanban (rápido y visible).
2. **Finanzas**: `total_cost`, margen y pestaña/vista (sin líneas de coste primero).
3. **Proceso de impresión**: modelo de pasos + pestaña en el formulario.
4. **Comunicaciones WhatsApp**: modelo de comunicaciones + pestaña + botón enlace.

---

## Cómo usar las mejoras (implementado en código)

### Termómetro en Kanban
- Ve a **CRM → Oportunidades** y elige la vista **Kanban**.
- En cada tarjeta verás el **porcentaje de avance** (0–100 %) según la etapa.
- Los colores de tarjeta siguen definidos por etapa (tu lógica actual).

### Proceso de impresión
- En la ficha de una oportunidad, abre la pestaña **Proceso de impresión**.
- Pulsa **Crear pasos por defecto** si la lista está vacía (aparece solo entonces).
- Edita cada paso: estado (Pendiente / En curso / Completado), fechas, responsable y notas.
- Así tienes trazabilidad desde diseño hasta entrega.

### Finanzas
- En la pestaña **Finanzas**:
  - **Resumen**: Ingreso previsto, Precio final, Coste total, Margen (importe y %).
  - **Teléfono WhatsApp**: opcional, para el botón "Abrir WhatsApp".
  - **Desglose de costes**: añade líneas (concepto, tipo, importe). Luego pulsa **Calcular coste total desde líneas** para actualizar el coste total y el margen.

### Comunicaciones WhatsApp
- En la pestaña **Comunicaciones WhatsApp** añade registros: fecha, dirección (enviado/recibido), tipo (seguimiento, cotización, etc.) y resumen.
- En el **encabezado** del formulario usa el botón **Abrir WhatsApp** para abrir el chat con el número del contacto (o el de **Teléfono WhatsApp** si lo rellenaste). Después de hablar, registra la comunicación en la pestaña.
