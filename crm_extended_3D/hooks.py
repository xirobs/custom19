# -*- coding: utf-8 -*-
"""Hooks de instalación CreArt 3DP (costos, etapas legacy, verificación)."""

SHEET_COST_DEFAULTS = {
    "internet_hour_cost": 1.35,
    "electricity_hour_cost": 0.29,
    "depreciation_hour_cost": 2.0,
    "capacity_increase_hour_cost": 4.0,
    "spare_parts_hour_cost": 0.5,
    "postprocess_consumables_cost": 10.0,
    "dead_time_hour_cost": 0.55,
    "fiscal_charge_percent": 16.0,
    "mo_concept_hour": 70.0,
    "mo_design_hour": 150.0,
    "mo_operation_hour": 50.0,
    "mo_post_hour": 100.0,
    "sale_markup_1_12": 2.0,
    "sale_markup_12_24": 1.75,
    "sale_markup_24_plus": 1.5,
}

CREART_STAGE_NAMES = {
    "Solicitud",
    "Conceptualización/Diseño",
    "Propuesta enviada",
    "Negociación / Seguimiento",
    "Ganada/Pendiente pago",
    "Imprimiendo",
    "PostProcesado",
    "Control de Calidad",
    "Listo para Entregar",
    "Entregado/Cerrado",
    "Bloque/Pausado",
}

LEGACY_STAGE_NAMES = {
    "Conceptualización / Propuesta Enviada",
    "Ganado / adelanto recibido",
    "Aprobada propuesta",
    "Ganado y pendiente de pago",
    "Diseño/Ajustes",
    "En la cola de impresión",
    "En la cola de impresion",
    "Postprocesado",
    "Listo para entregar",
    "Entregado/cerrado",
    "Bloqueado/En pausa",
    "Bloqueado/en pausa",
    "New",
    "Qualified",
    "Proposition",
    "Won",
    "Lost",
}


def post_init_hook(env):
    _sync_sheet_defaults(env)
    _cleanup_legacy_stages(env)


def _sync_sheet_defaults(env):
    config = env.ref("crm_extended_3D.print_cost_config_creart_default", raise_if_not_found=False)
    if config:
        config.write(SHEET_COST_DEFAULTS)


def _cleanup_legacy_stages(env):
    """Archiva etapas duplicadas o legacy sin oportunidades (instalación limpia online)."""
    Stage = env["crm.stage"].with_context(active_test=False)
    for stage in Stage.search([("name", "in", list(LEGACY_STAGE_NAMES))]):
        if not env["crm.lead"].search_count([("stage_id", "=", stage.id)]):
            stage.active = False
    # Etapas fuera del pipeline CreArt que quedaron vacías (p. ej. defaults CRM)
    for stage in Stage.search([("name", "not in", list(CREART_STAGE_NAMES))]):
        if stage.name in LEGACY_STAGE_NAMES:
            continue
        if env["crm.lead"].search_count([("stage_id", "=", stage.id)]):
            continue
        if stage.name not in CREART_STAGE_NAMES:
            stage.active = False
