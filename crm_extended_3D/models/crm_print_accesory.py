from odoo import models, fields, api

class CrmPrintAccessoryLine(models.Model):
    _name = 'crm.print.accessory.line'
    _description = 'Accesorios impresión 3D'

    lead_id = fields.Many2one(
        'crm.lead',
        ondelete='cascade'
    )

    product_id = fields.Many2one(
        'product.product',
        string="Accesorio",
        required=True
    )

    quantity = fields.Float(default=1)
    usage_mode = fields.Selection(
        [
            ('percent', 'Porcentaje de uso'),
            ('unit', 'Pieza completa'),
        ],
        string="Modo de uso",
        default='percent',
        required=True,
        help="Consumibles: porcentaje de uso del envase. Herrajes/pines: pieza completa por cantidad.",
    )
    usage_percent = fields.Float(
        string="% de uso",
        default=5.0,
        help="Porcentaje del costo del producto que realmente se usa (ej. 2% o 5%).",
    )

    price_unit = fields.Float(string="Costo unitario")

    subtotal = fields.Float(
        compute="_compute_subtotal",
        store=True
    )

    @api.depends('quantity', 'price_unit', 'usage_mode', 'usage_percent', 'product_id')
    def _compute_subtotal(self):
        for rec in self:
            if rec.usage_mode == 'percent':
                # Consumible: se usa una fracción del costo del envase.
                rec.subtotal = (rec.price_unit or 0.0) * ((rec.usage_percent or 0.0) / 100.0)
            else:
                # Pieza completa: costo por cantidad utilizada.
                rec.subtotal = (rec.quantity or 0.0) * (rec.price_unit or 0.0)

    @api.onchange('product_id')
    def _onchange_product_id_set_price(self):
        """Carga precio del producto al seleccionar accesorio."""
        for rec in self:
            if rec.product_id:
                rec.price_unit = rec.product_id.standard_price or rec.product_id.lst_price or 0.0