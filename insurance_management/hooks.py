# -*- coding: utf-8 -*-


def mark_insurance_opportunities(env):
    """Marca oportunidades que ya tienen oferta o póliza."""
    leads = env["crm.lead"].sudo().search([
        ("is_insurance_opportunity", "=", False),
        "|",
        ("insurance_scheme_id", "!=", False),
        ("insurance_policy_id", "!=", False),
    ])
    if leads:
        leads.write({"is_insurance_opportunity": True})


def post_init_hook(env):
    mark_insurance_opportunities(env)
    apply_brand(env)


BRAND_MARK = "data-o-ins-brand"
BRAND_HEADER = (
    '<div data-o-ins-brand="1" style="background:#5B47CC;background:linear-gradient(180deg,#8963CA,#4B3FCF);'
    'padding:16px 22px;border-radius:12px 12px 0 0;">'
    '<img src="/insurance_management/static/img/logo_h_light.png" alt="Respaldo Patrimonial · Asesores de Seguros" '
    'style="height:46px;width:auto;display:block;"/>'
    '</div>'
    '<div style="height:4px;background:#1AD3B1;background:linear-gradient(90deg,#1AD3B1,#208CEF);margin-bottom:12px;"></div>'
)


def brand_mail_templates(env):
    """Encabezado con el logo de la agencia en las plantillas de correo del módulo (una sola vez)."""
    data = env["ir.model.data"].sudo().search([
        ("module", "=", "insurance_management"), ("model", "=", "mail.template"),
    ])
    templates = env["mail.template"].sudo().browse(data.mapped("res_id")).exists()
    langs = ["en_US"] + [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    for template in templates:
        for lang in langs:
            tmpl = template.with_context(lang=lang)
            body = tmpl.body_html or ""
            if not body or BRAND_MARK in body:
                continue
            tmpl.body_html = BRAND_HEADER + body


def apply_brand(env):
    """Logo y colores de la agencia: en la empresa (si solo hay una) y en los correos."""
    companies = env["res.company"].sudo().search([])
    if len(companies) == 1:
        env["insurance.brand"].apply_to_company(companies)
    brand_mail_templates(env)
