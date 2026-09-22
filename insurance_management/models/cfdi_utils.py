# -*- coding: utf-8 -*-

"""Helpers CFDI 4.0: usan l10n_mx_edi cuando está instalado."""

CFDI_USAGE_GMM = "D07"
CFDI_USAGE_GENERAL = "G03"
CFDI_USAGE_PAYMENT = "CP01"
SAT_PRODUCT_INSURANCE = "84131500"
SAT_PRODUCT_BILLING = "84111506"


def module_installed(env, name):
    return bool(env["ir.module.module"].sudo().search_count([
        ("name", "=", name),
        ("state", "=", "installed"),
    ]))


def apply_cfdi_invoice_values(env, vals, *, usage, payment_method, payment_form):
    """Escribe claves SAT en la factura si existen los campos de l10n_mx_edi."""
    Move = env["account.move"]
    fields_map = Move._fields
    if "l10n_mx_edi_usage" in fields_map:
        vals["l10n_mx_edi_usage"] = usage
    if "l10n_mx_edi_payment_policy" in fields_map:
        vals["l10n_mx_edi_payment_policy"] = payment_method
    if "l10n_mx_edi_payment_method" in fields_map:
        vals["l10n_mx_edi_payment_method"] = payment_method
    if payment_form and "l10n_mx_edi_payment_method_id" in fields_map:
        method = env["l10n_mx_edi.payment.method"].sudo().search(
            [("code", "=", payment_form)],
            limit=1,
        )
        if method:
            vals["l10n_mx_edi_payment_method_id"] = method.id
    return vals


def apply_sat_product_code(product):
    if not product:
        return
    code = SAT_PRODUCT_INSURANCE
    if "l10n_mx_edi_code_sat_id" in product._fields:
        sat = product.env["l10n_mx_edi.product.sat.code"].sudo().search(
            [("code", "=", code)],
            limit=1,
        )
        if sat and not product.l10n_mx_edi_code_sat_id:
            product.l10n_mx_edi_code_sat_id = sat.id
    if "unspsc_code_id" in product._fields and not product.unspsc_code_id:
        unspsc = product.env["product.unspsc.code"].sudo().search(
            [("code", "=", code)],
            limit=1,
        )
        if unspsc:
            product.unspsc_code_id = unspsc.id
