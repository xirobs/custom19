# -*- coding: utf-8 -*-
"""Seguimiento de altas: quién cargó a cada asegurado, cuándo, y si su pago ya se recibió o remitió.
Sirve para armar la lista de nuevos a asegurar (los que aún no tienen certificado)."""
from odoo import api, fields, models

PAY_STATUS = [
    ('unpaid', 'Sin pago'),
    ('paid', 'Pagado'),
    ('remitted', 'Remitido a aseguradora'),
]


class RssInsuredTracking(models.Model):
    _inherit = 'rss.insured'

    payment_ids = fields.Many2many('rss.payment', 'rss_payment_insured_rel', 'insured_id', 'payment_id',
                                   string='Pagos', readonly=True)
    loaded_by_id = fields.Many2one('res.users', string='Cargado por', compute='_compute_loaded_by', store=True,
                                   index=True)
    load_date = fields.Date('Fecha de carga', compute='_compute_loaded_by', store=True, index=True)
    pay_status = fields.Selection(PAY_STATUS, string='Pago', compute='_compute_pay_status', store=True,
                                  index=True)
    paid_date = fields.Date('Fecha de pago', compute='_compute_pay_status', store=True)
    pay_reference = fields.Char('Recibo / folio de pago', compute='_compute_pay_status', store=True)
    pay_method = fields.Char('Forma de pago', compute='_compute_pay_status', store=True)
    pay_registered_by_id = fields.Many2one('res.users', string='Registró el pago', compute='_compute_pay_status',
                                           store=True)

    @api.depends('captured_by_id', 'create_uid', 'create_date')
    def _compute_loaded_by(self):
        for rec in self:
            rec.loaded_by_id = rec.captured_by_id or rec.create_uid
            rec.load_date = rec.create_date.date() if rec.create_date else False

    @api.depends('payment_ids.state', 'payment_ids.date', 'payment_ids.name',
                 'payment_ids.payment_method', 'payment_ids.received_by', 'payment_ids.reference',
                 'import_batch_id.state', 'import_batch_id.office_load', 'import_batch_id.paid_date')
    def _compute_pay_status(self):
        for rec in self:
            pays = rec.payment_ids.filtered(lambda p: p.state in ('confirmed', 'remitted')).sorted(
                lambda p: (p.date or fields.Date.today(), p.id))
            batch = rec.import_batch_id
            batch_paid = batch and not batch.office_load and batch.state == 'done'
            if pays:
                last = pays[-1]
                rec.pay_status = 'remitted' if any(p.state == 'remitted' for p in pays) else 'paid'
                rec.paid_date = last.date
                rec.pay_reference = last.name
                method = dict(last._fields['payment_method'].selection).get(last.payment_method, '')
                rec.pay_method = ('%s · %s' % (method, last.reference)) if last.reference else method
                rec.pay_registered_by_id = last.received_by or last.create_uid
            elif batch_paid:
                rec.pay_status = 'paid'
                rec.paid_date = batch.paid_date.date() if batch.paid_date else False
                rec.pay_reference = batch.reference or batch.display_name
                rec.pay_method = batch.payment_method or ''
                rec.pay_registered_by_id = batch.write_uid
            else:
                rec.pay_status = 'unpaid'
                rec.paid_date = False
                rec.pay_reference = False
                rec.pay_method = False
                rec.pay_registered_by_id = False
