"""Control de materiales e inventario para oportunidades CreArt 3DP."""

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CrmLeadMaterialLine(models.Model):
    _name = "crm.lead.material.line"
    _description = "Material requerido por oportunidad 3D"
    _order = "material_type, id"

    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", string="Producto", required=True)
    material_type = fields.Selection([
        ("filament", "Filamento"), ("accessory", "Accesorio"),
        ("consumable", "Consumible"), ("other", "Otro"),
    ], default="other")
    quantity = fields.Float(string="Cantidad", digits="Product Unit", required=True, default=1.0)
    uom_id = fields.Many2one("uom.uom", related="product_id.uom_id", store=True, readonly=True)
    qty_available = fields.Float(string="Disponible", compute="_compute_stock_info", digits="Product Unit")
    qty_reserved = fields.Float(string="Reservado", default=0.0)
    qty_consumed = fields.Float(string="Consumido", default=0.0)
    state = fields.Selection([
        ("draft", "Borrador"), ("reserved", "Reservado"), ("consumed", "Consumido"),
    ], default="draft")
    notes = fields.Char(string="Notas")

    @api.depends("product_id", "lead_id.company_id")
    def _compute_stock_info(self):
        for line in self:
            if not line.product_id:
                line.qty_available = 0.0
                continue
            product = line.product_id.with_company(line.lead_id.company_id or self.env.company)
            line.qty_available = product.qty_available


class CrmLeadMaterials(models.Model):
    _inherit = "crm.lead"

    material_line_ids = fields.One2many("crm.lead.material.line", "lead_id", string="Materiales requeridos")
    picking_ids = fields.One2many("stock.picking", "creart_lead_id", string="Movimientos de inventario")
    material_state = fields.Selection([
        ("none", "Sin materiales"), ("draft", "Pendiente"),
        ("reserved", "Reservado"), ("consumed", "Consumido"),
    ], compute="_compute_material_state", store=True)
    material_shortage = fields.Boolean(string="Faltante de stock", compute="_compute_material_state", store=True)

    @api.depends("material_line_ids.state", "material_line_ids.qty_available", "material_line_ids.quantity")
    def _compute_material_state(self):
        for lead in self:
            lines = lead.material_line_ids
            if not lines:
                lead.material_state = "none"
                lead.material_shortage = False
                continue
            states = set(lines.mapped("state"))
            if states == {"consumed"}:
                lead.material_state = "consumed"
            elif "reserved" in states or "consumed" in states:
                lead.material_state = "reserved"
            else:
                lead.material_state = "draft"
            lead.material_shortage = any(l.qty_available < l.quantity for l in lines if l.state != "consumed")

    def action_update_materials_from_job(self):
        MaterialLine = self.env["crm.lead.material.line"]
        for lead in self:
            lead.material_line_ids.filtered(lambda l: l.state == "draft").unlink()
            if lead.filament_id and lead.filament_grams:
                MaterialLine.create({
                    "lead_id": lead.id, "product_id": lead.filament_id.id,
                    "material_type": "filament", "quantity": lead.filament_grams / 1000.0,
                })
            for accessory in lead.accessory_ids:
                qty = accessory.quantity or 1.0
                if accessory.usage_mode == "percent":
                    qty = (accessory.usage_percent or 0.0) / 100.0
                MaterialLine.create({
                    "lead_id": lead.id, "product_id": accessory.product_id.id,
                    "material_type": "accessory", "quantity": qty,
                })
        return True

    def action_reserve_materials(self):
        for lead in self:
            if not lead.material_line_ids:
                lead.action_update_materials_from_job()
            shortages = []
            for line in lead.material_line_ids.filtered(lambda l: l.state == "draft"):
                if line.qty_available < line.quantity:
                    shortages.append(f"{line.product_id.display_name}: req. {line.quantity}, disp. {line.qty_available}")
                else:
                    line.write({"state": "reserved", "qty_reserved": line.quantity})
            if shortages:
                raise UserError(_("No hay stock suficiente:\n%s") % "\n".join(shortages))
        return True

    def action_consume_materials(self):
        Picking = self.env["stock.picking"]
        Move = self.env["stock.move"]
        for lead in self:
            reserved = lead.material_line_ids.filtered(lambda l: l.state == "reserved")
            if not reserved:
                raise UserError(_("No hay materiales reservados."))
            warehouse = self.env["stock.warehouse"].search([("company_id", "=", lead.company_id.id or self.env.company.id)], limit=1)
            if not warehouse:
                raise UserError(_("Configure un almacén."))
            picking_type = self.env["stock.picking.type"].search([("code", "=", "outgoing"), ("warehouse_id", "=", warehouse.id)], limit=1)
            customer_loc = self.env.ref("stock.stock_location_customers")
            picking = Picking.create({
                "picking_type_id": picking_type.id,
                "location_id": warehouse.lot_stock_id.id,
                "location_dest_id": customer_loc.id,
                "origin": f"CreArt - {lead.lead_sequence or lead.name}",
                "creart_lead_id": lead.id,
            })
            for line in reserved:
                Move.create({
                    "name": line.product_id.display_name,
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.quantity,
                    "product_uom": line.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": warehouse.lot_stock_id.id,
                    "location_dest_id": customer_loc.id,
                })
            picking.action_confirm()
            picking.button_validate()
            reserved.write({"state": "consumed", "qty_consumed": reserved.mapped("quantity")})
        return True

    def action_open_material_pickings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Movimientos de materiales"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("creart_lead_id", "=", self.id)],
        }


class StockPicking(models.Model):
    _inherit = "stock.picking"

    creart_lead_id = fields.Many2one("crm.lead", string="Oportunidad CreArt", index=True, ondelete="set null")
