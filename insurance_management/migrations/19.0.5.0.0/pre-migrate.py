# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Se elimina la forma de pago 'Pago de contado' (unique): pasa a Anual."""
    if not version:
        return
    cr.execute(
        "UPDATE insurance_policy SET premium_type = 'yearly' WHERE premium_type = 'unique'"
    )
    _logger.info("insurance_management: %s pólizas de contado pasaron a forma de pago Anual", cr.rowcount)
