# BNI - Control de Asistencia (Odoo 19) — v19.0.2.0.0

## Flujo
1. **Miembros** (BNI › Miembros): nombre, empresa, giro, teléfono, correo y *código de miembro*.
   - **Alta**: al crear el miembro se registra su alta (fecha, padrino / quién lo invitó, renovación, información de alta).
   - **Baja** (botón *Dar de baja*): fecha, motivo y detalle. Sale de las listas abiertas y deja de aparecer en el QR.
   - **Reingreso**: lo reactiva y lo agrega a los eventos abiertos.
   - Historial en *BNI › Miembros › Altas y bajas*. Los nuevos (90 días) muestran la etiqueta *Nuevo*.
2. **Evento** (BNI › Asistencia › Eventos): nombre, modalidad, fecha y hora de llegada y tipo de QR.
   - *Abrir registro* carga a los miembros **activos** como *Pendiente*.
   - El registro **sigue abierto durante todo el evento**: quien llega después de la hora queda como *Retardo* (con minutos).
   - *Cerrar evento* es manual: hazlo al terminar la sesión; quien no registró queda como *Falta*.
   - Duplicar el evento crea la sesión de la semana siguiente.
3. **QR**
   - **Dinámico** (recomendado, sobre todo en virtual): *Mostrar QR en pantalla* y compartir pantalla en la sesión.
     Cambia cada minuto; un QR fotografiado o reenviado deja de abrir la página a los 3 minutos.
   - **Fijo**: para imprimir en la sala.
4. **En el celular**: el miembro se identifica (código, correo o teléfono) y su celular lo recuerda: las siguientes veces es un clic.
   Puede dejar su **observación** al registrarse o después. También se registran ahí **sustitutos** (a quién sustituyen,
   nombre y empresa) e **invitados** (nombre, empresa y quién los invitó).

## Indicadores
- % Participación = (A tiempo + Retardo + Sustituto) / (Sesiones cerradas − Justificadas)
- % Puntualidad = A tiempo / (A tiempo + Retardo)

## Reportes
- PDF por evento, PDF por mes/periodo (matriz miembro × sesión + altas y bajas), análisis pivote/gráfico.

## Seguridad y privacidad
- Grupos *BNI: Asistencia (Usuario)* y *BNI: Administrador*; reglas multiempresa.
- Las páginas públicas solo muestran nombres de miembros activos (sin teléfonos ni correos) y exigen POST + CSRF.

## Instalación
Odoo 19 (Community o Enterprise, on-premise). Requiere la librería python `qrcode` (incluida en Odoo).
