"""Extension principal de CRM para operaciones de impresion 3D."""

from odoo import models, fields, api, _  # type: ignore
from datetime import timedelta

class CrmExtended3D(models.Model): 
    """Amplia `crm.lead` con operaciones, costos y seguimiento 3D."""

    _inherit = 'crm.lead'  # type: ignore
    _description = 'CRM Lead 3D Printing Extended'
    
    # ---------------------------------------------------------
    # SECCION 1: Operacion comercial y flujo de oportunidad
    # ---------------------------------------------------------
    sequence = fields.Integer(
        string="Orden en cola",
        default=10,
        index=True,
        help="Orden del trabajo en la cola de impresión"
    )

    _order = "priority desc, sequence asc, id desc"
    target_company_id = fields.Many2one(comodel_name='res.company', string='Target Company',help="Select the company to duplicate the opportunity to when won.")  # type: ignore
    product_id = fields.Many2one('product.product', string='Product')  # type: ignore
    quantity = fields.Float(string='Quantity', default=1.0)
    order_date = fields.Date(string='Fecha de pedido',default=fields.Date.today())
    delivery_date = fields.Date(string='Fecha de entrega',compute="_compute_delivery_date",store=True)
    advance_payment = fields.Float(string="Abono del Cliente")
    remaining_payment = fields.Float(string="Restante",compute="_compute_remaining",store=True)

    payment_method = fields.Selection(
        [
            ('cash', 'Efectivo'),
            ('transfer', 'Transferencia'),
            ('card', 'Tarjeta'),
            ('mercado_pago', 'Mercado Pago'),
            ('paypal', 'PayPal')
        ],
        string="Método de Pago"
    )

    client_type = fields.Selection(
        [
            ('b2b', 'B2B'),
            ('b2c', 'B2C'),
        ],
        string='Tipo de cliente',
    )

    buyer_persona = fields.Selection(
        [
            ('food_beverage', 'Alimentos y bebidas'),
            ('health', 'Salud'),
            ('insurance', 'Seguros'),
            ('construction_realestate', 'Construcción e inmobiliario'),
            ('hospitality_events', 'Hospitalidad y eventos'),
            ('education', 'Educación'),
            ('corporate', 'Corporativo'),
            ('families', 'Familias'),
            ('niche_hobby', 'Nicho/hobby'),
            ('entrepreneurs', 'Emprendedores'),
            ('gifts_celebrations', 'Regalos y celebraciones'),
        ],
        string='Buyer persona',
    )

    sector_tag_ids = fields.Many2many(
        'crm.tag',
        'crm_lead_creart_sector_tag_rel',
        'lead_id',
        'tag_id',
        string='Por sector',
        domain=[('creart_tag_group', '=', 'sector')],
    )

    urgency_tag_ids = fields.Many2many(
        'crm.tag',
        'crm_lead_creart_urgency_tag_rel',
        'lead_id',
        'tag_id',
        string='Por urgencia',
        domain=[('creart_tag_group', '=', 'urgency')],
    )

    priority = fields.Selection(
        [
            ("0", "Baja"),
            ("1", "Normal"),
            ("2", "Alta"),
            ("3", "Urgente"),
        ],
        default="1"
    )


    lead_sequence = fields.Char(string='Nro. Solicitud',readonly=True,copy=False,default=lambda self: _('New'))    
    
    @api.depends('order_date')
    def _compute_delivery_date(self):

        for rec in self:

            if not rec.order_date:
                rec.delivery_date = False
                continue

            days = 0
            current_date = rec.order_date

            while days < 6:
                current_date += timedelta(days=1)

                if current_date.weekday() < 5:
                    days += 1

            rec.delivery_date = current_date
    
    @api.depends('selected_total_price', 'advance_payment')
    def _compute_remaining(self):

        for rec in self:
            rec.remaining_payment = (rec.selected_total_price or 0.0) - (rec.advance_payment or 0.0)
    
    #asignación fija de moneda en las oportunidades
    def _default_currency(self):
        return self.env.ref('base.USD').id  # type: ignore
    
    currency_id = fields.Many2one('res.currency',string='Moneda oportunidad',default=_default_currency)  # type: ignore
    
    # Nombre de la etapa (para vistas/dominios sin acceder a stage_id.name)
    stage_name = fields.Char(related='stage_id.name', string='Stage Name', readonly=True)  # type: ignore

    # Iniciamos a conectar las clases con la lista de seleccion
    source_id = fields.Many2one('source', string='Source', ondelete='set null',) 
    # Campos informativos (Project / Cotizaciones)
    description_place = fields.Text(string='Site address', help="Set Description for place")
    date_from = fields.Date(string='Install date', help="Starting Date for Project Execution")
    # Geolocation
    geolocation_latitude = fields.Float(string='Latitude', default=12)
    geolocation_longitude = fields.Float(string='Longitude', default=12)

    # Campo One2many para los productos cotizados
    quoted_product_ids = fields.One2many('crm.quoted.product', 'lead_id', string='Quoted Products')

    # ---------------------------------------------------------
    # SECCION 2: Control de impresion 3D y KPIs
    # ---------------------------------------------------------
    # Termómetro: porcentaje de avance según etapa (0-100) para mostrar en Kanban
    stage_progress = fields.Integer(
        string='Avance (%)',
        compute='_compute_stage_progress',
        store=True,
        help="Porcentaje de avance en el pipeline (termómetro). Se usa en la vista Kanban."
    )
    # Control gastos/ingresos
    margin_amount = fields.Float(
        string='Margen',
        compute='_compute_profitability',
        store=True,
        digits='Product Price',
    )
    # `margin_percent` se declara mas adelante para mantener compatibilidad
    # con la nomenclatura usada en vistas existentes del modulo.
    # WhatsApp: teléfono para enlace directo (si no se usa el del contacto)
    whatsapp_phone = fields.Char(
        string='Teléfono WhatsApp',
        help="Número para contacto por WhatsApp (con código país, sin +). Si está vacío se usa el del contacto."
    )
    # Relaciones con nuevos modelos
    print_process_step_ids = fields.One2many(
        'crm.print.process.step',
        'lead_id',  # type: ignore
        string='Pasos proceso impresión',
        help="Pasos del proceso desde diseño hasta entrega."
    )
    cost_line_ids = fields.One2many(
        'crm.lead.cost.line',
        'lead_id',
        string='Líneas de coste',
        help="Desglose de costes (material, mano de obra, etc.)."
    )
    whatsapp_communication_ids = fields.One2many(
        'crm.whatsapp.communication',
        'lead_id',
        string='Comunicaciones WhatsApp',
        help="Historial de comunicaciones y seguimientos por WhatsApp."
    )
    
    # Campo para seleccionar la compañía destino
    create_lead_company = fields.Boolean(string='¿Desea convertir en oportunidad para la otra compañia?', help="Convierte esta oportunidad ganada en un leads para multicompañia.")

    printer_id = fields.Many2one('maintenance.equipment',string="Impresora")

    filament_color = fields.Char(string="Color filamento")
    
    margin = fields.Float(
        string="Margen",
        compute="_compute_profitability",
        store=True
    )

    margin_percent = fields.Float(
        string="Margen %",
        compute="_compute_profitability",
        store=True
    )

    rentable = fields.Boolean(
        string="Trabajo rentable",
        compute="_compute_profitability",
        store=True
    )
    
    @api.depends('selected_total_price', 'total_cost')
    def _compute_profitability(self):
        """
        Calcula rentabilidad consolidada para todos los campos de margen.
        `selected_total_price` es el precio cotizado según cantidad.
        """
        for rec in self:
            revenue = rec.selected_total_price or 0.0
            cost = rec.total_cost or 0.0
            margin_value = revenue - cost

            rec.margin = margin_value
            rec.margin_amount = margin_value
            rec.margin_percent = (margin_value / revenue * 100) if revenue else 0.0
            rec.rentable = margin_value > 0
    
    
    @api.model_create_multi
    def create(self, vals_list):
        """Asigna secuencia de solicitud y sincroniza color por etapa."""
        for vals in vals_list:
            if not vals.get('lead_sequence') or vals.get('lead_sequence') == _('New'):
                vals['lead_sequence'] = self.env['ir.sequence'].next_by_code('crm.lead.sequence') or _('New')
        records = super().create(vals_list)
        records._sync_color_with_stage()
        records._sync_creart_tags()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'stage_id' in vals:
            self._sync_color_with_stage()
        if {'sector_tag_ids', 'urgency_tag_ids'} & set(vals):
            self._sync_creart_tags()
        return res

    def _sync_creart_tags(self):
        """Mantiene tag_ids alineado con etiquetas CreArt por sector y urgencia."""
        for lead in self:
            other_tags = lead.tag_ids.filtered(lambda tag: not tag.creart_tag_group)
            lead.tag_ids = other_tags | lead.sector_tag_ids | lead.urgency_tag_ids

    def manager_approve(self):
        """Acción de aprobación por el gestor (avance de etapa / confirmación)."""
        self.ensure_one()
        return True

    # ---------------------------------------------------------
    # SECCION 3: Computados y acciones de soporte
    # ---------------------------------------------------------
    @api.depends('stage_id', 'stage_id.sequence')  # type: ignore
    def _compute_stage_progress(self):
        """
        Calcula el porcentaje de avance (0-100) según la etapa.
        Mapeo por nombre de etapa para alinearlo con tu pipeline de impresión 3D.
        """
        # Orden de etapas de menor a mayor avance (nombre -> %)
        stage_progress_map = {
            'Solicitud': 0,
            'Conceptualización/Diseño': 10,
            'Propuesta enviada': 25,
            'Negociación / Seguimiento': 30,
            'Ganada/Pendiente pago': 45,
            'Imprimiendo': 65,
            'PostProcesado': 75,
            'Postprocesado': 75,
            'Control de Calidad': 85,
            'Listo para Entregar': 92,
            'Listo para entregar': 92,
            'Entregado/Cerrado': 100,
            'Entregado/cerrado': 100,
            'Bloque/Pausado': 0,
            # Compatibilidad con nombres anteriores
            'Diseño/Ajustes': 10,
            'Aprobada propuesta': 35,
            'Ganado y pendiente de pago': 45,
            'En la cola de impresión': 55,
            'En la cola de impresion': 55,
            'Bloqueado/En pausa': 0,
            'Bloqueado/en pausa': 0,
        }
        for lead in self:
            if lead.stage_id:  # type: ignore
                lead.stage_progress = stage_progress_map.get(
                    lead.stage_id.name,  # type: ignore
                    min(100, (lead.stage_id.sequence or 0)),  # type: ignore
                )
            else:
                lead.stage_progress = 0

    def action_compute_total_cost_from_lines(self):
        """Actualiza el coste total con la suma de las líneas de coste."""
        for lead in self:
            lead.total_cost = sum(lead.cost_line_ids.mapped('amount'))

    def action_create_default_process_steps(self):
        """
        Crea los pasos por defecto del proceso de impresión (diseño → entrega)
        si aún no existen. Se puede llamar desde un botón en la pestaña.
        """
        self.ensure_one()
        if self.print_process_step_ids:
            return
        Step = self.env['crm.print.process.step']  # type: ignore
        default_steps = [
            (10, 'Diseño / Ajustes'),
            (20, 'En cola de impresión'),
            (30, 'Imprimiendo'),
            (40, 'Postprocesado'),
            (50, 'Control de calidad'),
            (60, 'Listo para entregar'),
            (70, 'Entregado'),
        ]
        for seq, name in default_steps:
            Step.create({'lead_id': self.id, 'name': name, 'sequence': seq})

    def action_open_whatsapp(self):
        """
        Abre WhatsApp (Web o App) con el número del contacto.
        Usa whatsapp_phone si está definido; si no, el teléfono del partner.
        No envía mensajes desde Odoo; solo abre el enlace.
        """
        self.ensure_one()
        phone = self.whatsapp_phone or (self.partner_id and self.partner_id.phone)
        if not phone:
            return
        # Limpiar caracteres no numéricos (código país sin +)
        phone = ''.join(c for c in str(phone) if c.isdigit())
        if not phone:
            return
        url = f'https://wa.me/{phone}'
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def _sync_color_with_stage(self):
        """Sincroniza el color del kanban según la etapa (termómetro de avance)."""
        stage_color_map = {
            'Solicitud': 3,                     # Azul claro
            'Conceptualización/Diseño': 5,      # Morado
            'Propuesta enviada': 2,             # Amarillo
            'Negociación / Seguimiento': 2,     # Amarillo
            'Ganada/Pendiente pago': 4,         # Naranja
            'Imprimiendo': 1,                   # Azul fuerte
            'PostProcesado': 10,                # Verde claro
            'Control de Calidad': 9,            # Verde
            'Listo para Entregar': 8,           # Verde intenso
            'Entregado/Cerrado': 0,             # Gris
            'Bloque/Pausado': 6,                # Rojo

            # Compatibilidad con nombres anteriores
            'Diseño/Ajustes': 5,
            'Aprobada propuesta': 2,
            'Ganado y pendiente de pago': 4,
            'En la cola de impresión': 7,
            'En la cola de impresion': 7,
            'Postprocesado': 10,
            'Listo para entregar': 8,
            'Entregado/cerrado': 0,
            'Bloqueado/En pausa': 6,
            'Bloqueado/en pausa': 6,
        }
        for lead in self:
            if lead.stage_id:
                color = stage_color_map.get(lead.stage_id.name)
                if color is not None:
                    lead.color = color

class Source(models.Model):
    _name = "source"    

    name = fields.Char(string='Source')

class QuotedProduct(models.Model):
    _name = 'crm.quoted.product'
    _description = 'Quoted Product'
    # Campo para agregar productos al duplicar la oportunidad
    lead_id = fields.Many2one('crm.lead', string='Opportunity', required=True, ondelete='cascade')  # type: ignore
    product_id = fields.Many2one('product.product', string='Product', required=True)  # type: ignore
    quantity = fields.Float(string='Quantity', default=1.0)