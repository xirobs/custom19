# Estaciones de Carga - Módulo Odoo 19

Módulo Odoo 19 Enterprise para gestionar un negocio de estaciones de carga de celulares
con dispositivos ESP32 distribuidos.

## Características

- **Gestión de estaciones físicas** con código único, ubicación, número de puertos
- **Flujo de pago completo** integrado con Pago Móvil, Binance Pay, USDT TRC20, Zelle y Efectivo
- **API REST para ESP32** con autenticación por API key + tokens HMAC firmados
- **Facturación automática** por cada carga (integrada con `account.move`)
- **Surge pricing automático** durante apagones (detección por heartbeats en batería)
- **Códigos offline** para operar sin internet
- **Dashboard kanban** con estado en tiempo real de cada estación
- **Multi-operador** con reglas de seguridad por estación
- **Reportes pivot y graph** integrados

## Instalación

### 1. Copiar el módulo al servidor

Copia la carpeta `estaciones_carga/` a tu directorio `addons` de Odoo. Si tienes
Odoo Enterprise en Contabo, la ruta típica es:

```bash
# Si usaste el script oficial de instalación
/opt/odoo/custom-addons/estaciones_carga/

# Si usaste Docker
/var/lib/odoo/addons/estaciones_carga/
```

Vía SCP desde tu máquina:
```bash
scp -r estaciones_carga/ usuario@tu-contabo:/opt/odoo/custom-addons/
```

### 2. Verificar permisos

```bash
sudo chown -R odoo:odoo /opt/odoo/custom-addons/estaciones_carga
sudo chmod -R 755 /opt/odoo/custom-addons/estaciones_carga
```

### 3. Reiniciar Odoo

```bash
sudo systemctl restart odoo
```

### 4. Instalar desde la interfaz

1. Modo desarrollador: `Ajustes → Activar modo desarrollador`
2. `Apps → Actualizar lista de aplicaciones`
3. Buscar **"Estaciones de Carga"** → **Instalar**

## Configuración inicial

### 1. Configurar precios y métodos de pago

`Estaciones de Carga → Configuración`

- Precios base estándar y rápida (USD)
- Multiplicador de surge pricing (default 1.5x)
- Datos de Pago Móvil (banco, teléfono, cédula)
- Email Zelle, dirección USDT TRC20
- Tasa BCV (puedes automatizarla con un cron que consulte el BCV)

### 2. Crear tu primera estación

`Estaciones de Carga → Estaciones → Crear`

Campos importantes:
- **Código**: identificador único, ej. `EST-001`
- **Cantidad de puertos**: 1-16
- **Diario contable**: el diario de ventas que recibirá las facturas
- **Productos**: los productos `Carga estándar` y `Carga rápida` se crean automáticamente

Al guardar, se generan automáticamente:
- **API Key**: la copias al firmware del ESP32 (header `Authorization: Bearer ...`)
- **HMAC Secret**: la copias también al firmware para validar tokens

### 3. Configurar el ESP32

En el código del ESP32, actualizar:
```cpp
const char* SERVER_URL = "https://tu-odoo.contabo.com";
const char* ESTACION_CODIGO = "EST-001";
const char* API_KEY = "...";  // el campo api_key de la estación
const char* HMAC_SECRET = "...";  // el campo hmac_secret de la estación
```

## Endpoints API

### Para el ESP32

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/estacion/{codigo}/activaciones` | Polling de activaciones pendientes |
| POST | `/api/estacion/{codigo}/confirmar-aplicacion` | Confirma que activó el relé |
| POST | `/api/estacion/{codigo}/heartbeat` | Reporta estado cada minuto |
| POST | `/api/estacion/{codigo}/codigo-offline` | Valida código manual |

Todos requieren header: `Authorization: Bearer {api_key}`

### Para el cliente (webapp)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/pago/info-estacion/{codigo}` | Info de la estación y precios |
| POST | `/api/pago/iniciar` | Iniciar transacción de pago |
| POST | `/api/pago/confirmar` | Confirmar con referencia de pago |
| GET | `/api/pago/estado/{id}` | Verificar estado de carga |

### Webhooks

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/webhook/binance-pay` | Confirmación automática de Binance |

## Crons automáticos

El módulo instala 3 crons:

| Cron | Frecuencia | Acción |
|------|------------|--------|
| Expirar transacciones | 5 min | Marca como expiradas las pendientes vencidas |
| Detectar apagones | 2 min | Activa surge pricing si la estación reporta en batería |
| Limpiar heartbeats | 1 día | Borra heartbeats de más de 30 días |

## Estructura de archivos

```
estaciones_carga/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── __init__.py
│   ├── esp32_api.py        # API para los ESP32
│   └── cliente_api.py      # API para la webapp del cliente
├── models/
│   ├── __init__.py
│   ├── estacion.py         # Modelo principal
│   ├── transaccion.py      # Transacciones + facturación
│   ├── heartbeat.py        # Monitoreo
│   ├── codigo_offline.py   # Códigos offline
│   └── res_config_settings.py
├── views/
│   ├── estacion_views.xml
│   ├── transaccion_views.xml
│   ├── heartbeat_views.xml
│   ├── codigo_offline_views.xml
│   ├── config_settings_views.xml
│   ├── dashboard_views.xml
│   └── menu.xml
├── wizards/
│   ├── __init__.py
│   ├── generar_codigos_offline_wizard.py
│   └── generar_codigos_offline_wizard.xml
├── security/
│   ├── estaciones_carga_security.xml
│   └── ir.model.access.csv
└── data/
    ├── sequences.xml
    ├── product_data.xml
    └── cron_jobs.xml
```

## Configuración de workers en Odoo

Para soportar el polling de muchos ESP32 simultáneamente, edita `/etc/odoo/odoo.conf`:

```ini
workers = 4              # ajusta según CPU del Contabo (2 por core)
limit_request = 8192
limit_time_cpu = 600
limit_time_real = 1200
max_cron_threads = 2
```

Y reinicia: `sudo systemctl restart odoo`

## Próximas mejoras

1. **Webapp cliente (Next.js o vanilla JS)** que consume los endpoints `/api/pago/*`
2. **Integración real con Pago Móvil** (vía API bancaria o lector de SMS)
3. **Validación de transacciones blockchain** para USDT
4. **App móvil del operador** para vender códigos offline desde el teléfono
5. **Notificaciones WhatsApp Business** al cliente cuando se activa el puerto
6. **Multi-company** para franquicias

## Licencia

LGPL-3
