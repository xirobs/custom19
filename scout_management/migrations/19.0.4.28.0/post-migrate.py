# -*- coding: utf-8 -*-
"""La comisión del pago en línea se cobra siempre que se pague por Mercado Pago (antes solo arriba de $100)."""


def migrate(cr, version):
    cr.execute("UPDATE res_company SET scout_online_fee_min = 0 WHERE scout_online_fee_min = 100")
