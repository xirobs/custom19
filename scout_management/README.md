# Gestión de Grupo Scout (`scout_management`) v2 — Odoo 19

Este módulo, hecho para el Grupo 7 Ethnos Puebla, se integra a los módulos estándar de Odoo:

| Necesidad | Módulo de Odoo |
|---|---|
| Miembros con expediente completo | **Contactos**: pestañas Scout, Ficha médica, Adelantos e insignias, Registro y adeudos |
| Actividades del grupo o de cada sección | **Calendario**: responsables, temática, objetivos, horario, descripción, asistencia y casilla "Realizada" |
| Tesorería y balance para el Consejo | **Contabilidad**: cajas y bancos, cargos a miembros (CxC), facturas de proveedor (CxP) y pagos |
| Actas y chat con las familias | **Conversaciones** (mail): actas con acuerdos e hilos con los padres |

Depende de `contacts`, `calendar`, `account`, `portal` y `mail`.

## Instalación
1. Copia `scout_management` a tus addons, actualiza la lista de aplicaciones e instala **Gestión de Grupo Scout**.
2. En **Contabilidad**:
   - Ten un plan de cuentas y al menos un diario de **Efectivo** (Caja) y uno de **Banco**.
   - Captura los saldos iniciales con un asiento de apertura.
   - En *Grupo Scout › Configuración › Preparar cajas y bancos*, los cobros y pagos se configuran para afectar directo el saldo de cada caja o banco, sin conciliación bancaria.
3. En **Configuración**:
   - Crea el ciclo scout (2026, cuota de registro) y márcalo como actual.
   - Asigna a cada sección sus **dirigentes con acceso**.
   - Revisa edades, edad de paso y colores.
4. Sube el logo del grupo en *Ajustes › Compañía*.
5. Asigna los roles en *Ajustes › Usuarios* (categoría **Grupo Scout**).

## Roles y aprobaciones
| Rol | Acceso |
|---|---|
| **Jefe de grupo** | Acceso total y aprobaciones. Tiene el menú *Aprobaciones*. |
| **Secretaría** | Acceso total a miembros, fichas, calendario y actas. Consulta la contabilidad. |
| **Tesorero** | Lo mismo que Secretaría, más ingresos, egresos, cargos, facturas de proveedor y pagos. |
| **Jefe de sección** | Solo su sección: miembros con ficha médica, actividades, asistencia, adelantos, informe trimestral y chat con las familias. No ve otras secciones, la tesorería ni las actas. |
| **Padres (portal)** | Solo la información de su hijo, la ficha en línea, sus adeudos y el chat con los dirigentes de la sección. |

Puntos que requieren aprobación del **jefe de grupo** (le llega una actividad "Por hacer"):
- **Gastos y pagos:**
  - Egresos y traspasos de caja y bancos.
  - Facturas de proveedor.
  - Pagos salientes. Pagar una factura ya aprobada no pide una segunda aprobación.
  - Los ingresos se registran directo.
- **Actas:** Secretaría las envía a aprobación. Una vez aprobadas quedan bloqueadas y se notifica a los asistentes.
- **Fichas de registro:** Secretaría revisa y el jefe valida. Al validar se da de alta al miembro y se genera su cargo de registro.
- **Bajas y cambios de sección:** se piden con los botones del miembro. No se pueden cambiar directo.

## Flujo trimestral
1. En *Programa › Crear sesiones del trimestre* se crean los sábados en el Calendario. En cada actividad se capturan la temática, los responsables y los objetivos.
2. Cada sábado, en la pestaña **Asistencia** del evento: *Cargar miembros* → SI / NO / CA / M / B / N/A / "/" → *Cerrar lista*. La actividad queda marcada como realizada.
3. Los adelantos se registran en el contacto del miembro.
4. **Informe trimestral:** se crea y se presiona *Actualizar datos*.
   - Se calculan solos: registrados, pagados, constantes, intermitentes, rescate, Promesas, ruta de enlace, avances, equipos y actividades del calendario.
   - Se agregan a mano: objetivos, necesidades, objetivos clave y próximos pasos.
5. **Consejo de grupo:** imprime los informes de sección, el *Balance de tesorería* y el acta.

## PDFs (misma estructura que la ficha de registro del grupo)
- **Contactos:** Expediente scout.
- **Fichas:** Ficha única de registro, consentimiento y responsiva.
- **Calendario:** Programa trimestral de sección, Calendario del grupo y Lista de asistencia.
- **Grupo Scout:** Informe trimestral de sección.
- **Contabilidad:** Balance para el Consejo (ingresos por rubro, gastos, disponible, saldos, adeudos y gastos por aprobar).
- **Actas:** Acta de consejo o junta.
