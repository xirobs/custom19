# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Cuotas ya pagadas: registra el importe pagado para calcular la prima pagada."""
    if not version:
        return
    cr.execute(
        """
        UPDATE insurance_installment
           SET paid_amount = amount
         WHERE state = 'paid'
           AND COALESCE(paid_amount, 0) = 0
        """
    )
    _logger.info("insurance_management: %s cuotas pagadas actualizadas con su importe pagado", cr.rowcount)
    cr.execute(
        """
        UPDATE insurance_installment
           SET emission_date = date_from
         WHERE emission_date IS NULL
        """
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    policies = env["insurance.policy"].search([])
    Policy = env["insurance.policy"]
    for fname in ("amount_paid", "amount_due"):
        env.add_to_compute(Policy._fields[fname], policies)
    env.flush_all()
