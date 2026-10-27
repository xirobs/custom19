# Red Seguro Scout · «Rumbo Seguro» (Odoo 19)

Módulo para administrar el **seguro de accidentes personales** (fracturas, golpes, cortes, esguinces…)
que cubre a los miembros de los grupos scouts durante sus actividades. Parte de la familia **BitaScout**:
*BitaScout organiza, Red Seguro Scout protege.*

**Identidad:** emblema de Red Seguro Scout (escudo dorado con flor de lis roja sobre azul marino) como ícono
del módulo y sello de los documentos; lema **«Rumbo Seguro»**. Paleta: azul marino `#0A2A5E`, oro `#D9A93A`
y rojo `#B00625`.

**Personalidad del grupo:** en la ficha del grupo (pestaña *Identidad del grupo*) se captura su logo, colores y lema.
Prioridad en documentos: identidad del grupo → identidad de la organización (logo y colores de la empresa en
Ajustes › Diseño de documentos) → identidad de Red Seguro Scout. El sello «Red Seguro Scout · Rumbo Seguro»
siempre aparece al pie.

- Técnico: `red_seguro_scout` · Versión `19.0.1.0.0` · Depende de `base` y `mail` (Community y Enterprise)
- Probado en Odoo 19.0 limpio: instalación con y sin demo, 12 pruebas automáticas en verde.

## Menú

| Menú | Qué hace |
|---|---|
| Asegurados › Asegurados | Captura: primer y segundo nombre, apellido paterno y materno, fecha de nacimiento, edad (automática), sexo, grupo, rama, CURP opcional, tutor (nombre completo, parentesco, teléfono, correo). Lista, pivote y gráfica. |
| Asegurados › Grupos | Kanban con asegurados, hombres/mujeres y si ya pagó el año. Expediente PDF del grupo. |
| Asegurados › Agrupaciones | Distrito/provincia que reúne grupos. Sus PDF los ven todos sus grupos. Botón «Adjuntar PDF a sus grupos». |
| Seguro › Relaciones mensuales | Lista para la aseguradora: Excel, PDF y envío por correo con ambos adjuntos. |
| Seguro › Pagos de grupos | Pago del seguro por grupo → recibo PDF → remitido con folio/acuse de la aseguradora. |
| Seguro › Siniestros | Accidentes por asegurado, avisa si no tenía cobertura ese día. |
| Reportes › Asegurados por periodo | Pivote/gráfica de asegurados y **días cubiertos** por día, mes, trimestre y año. |
| Reportes › Comparativa de grupos | Gráfica de barras de grupos por cantidad de miembros (+ pivote por sexo, menores/adultos). |
| Configuración › Pólizas | Aseguradora, correo de envío, cuota por miembro, coberturas, día límite (10). |

## Relación mensual automática (días 1 al 10)

Un cron diario (06:00 hora de México) revisa cada póliza con *Generar relación automáticamente*:
si el día del mes está entre 1 y el **día límite** (10 por defecto) y la relación del mes no existe,
la genera. Luego:

- *Enviar automáticamente* apagado → deja una actividad «Enviar relación mensual» con vencimiento el día 10.
- *Enviar automáticamente* encendido → la manda por correo a la aseguradora con Excel + PDF.

Cada relación es una «foto» del mes: altas, bajas y días cubiertos por asegurado. Si va pasada del
día límite y no se ha enviado, aparece en rojo con la cinta **Vencida**.

## Flujo del pago

1. Grupo › **Registrar pago del seguro** (carga los miembros y calcula el importe esperado).
2. **Registrar pago y emitir recibo** → folio `REC/AAAA/0001`; los pre-registros pasan a asegurados.
3. **Imprimir recibo** (importe con letra, miembros cubiertos, firmas).
4. Captura el folio de la aseguradora → **Marcar remitido a aseguradora**.

Todo queda en el chatter con seguimiento de cambios.

## Seguridad

- **Red Seguro Scout / Capturista**: captura asegurados, grupos, pagos, relaciones y siniestros (no borra).
- **Red Seguro Scout / Administrador**: además pólizas, agrupaciones, cancelaciones y configuración.
- Reglas multiempresa en todos los modelos (`company_id in company_ids`).

## Instalación

1. Copia la carpeta `red_seguro_scout` a tu ruta de addons.
2. Reinicia Odoo, actualiza la lista de aplicaciones e instala **Red Seguro Scout**.
3. Configuración › Pólizas: completa aseguradora, número de póliza, correo para relaciones y cuota.
4. Para el PDF se requiere `wkhtmltopdf` (el mismo que usan los demás reportes de Odoo).

Pruebas: `odoo-bin -d <bd> -i red_seguro_scout --test-tags /red_seguro_scout --stop-after-init`
