# -*- coding: utf-8 -*-
"""Hooks de instalación CreArt 3DP (costos, dedupe de etapas, verificación)."""

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

# xmlid en crm_extended_data.xml → nombre canónico del pipeline
CREART_STAGE_XMLID_BY_NAME = {
    "Solicitud": "crm_stage_solicitud_recibida",
    "Conceptualización/Diseño": "crm_stage_en_diseno",
    "Propuesta enviada": "crm_stage_pendiente_aprobacion",
    "Negociación / Seguimiento": "crm_stage_negociacion_seguimiento",
    "Ganada/Pendiente pago": "crm_stage_pendiente_pago",
    "Imprimiendo": "crm_stage_imprimiendo",
    "PostProcesado": "crm_stage_post_proceso",
    "Control de Calidad": "crm_stage_control_calidad",
    "Listo para Entregar": "crm_stage_listo_entrega",
    "Entregado/Cerrado": "crm_stage_entregado_cerrado",
    "Bloque/Pausado": "crm_stage_bloqueado",
}


def post_init_hook(env):
    _sync_sheet_defaults(env)
    _dedupe_duplicate_stages(env)


def _sync_sheet_defaults(env):
    config = env.ref("crm_extended_3D.print_cost_config_creart_default", raise_if_not_found=False)
    if config:
        config.write(SHEET_COST_DEFAULTS)


def _dedupe_duplicate_stages(env):
    """Fusiona etapas repetidas (mismo nombre) sin borrar columnas vacías del pipeline.

    Al reinstalar/actualizar a veces quedan dos ``crm.stage`` con el mismo nombre.
    Se conserva el registro del XML del módulo (o el que tenga más oportunidades),
    se reasignan los leads y se elimina el duplicado.
    """
    Stage = env["crm.stage"]
    Lead = env["crm.lead"]

    stages = Stage.search([("name", "in", list(CREART_STAGE_NAMES))], order="id")
    by_name = {}
    for stage in stages:
        by_name.setdefault(stage.name, Stage.browse())
        by_name[stage.name] |= stage

    for name, group in by_name.items():
        if len(group) <= 1:
            continue
        keeper = _pick_stage_keeper(env, name, group)
        for duplicate in group - keeper:
            Lead.search([("stage_id", "=", duplicate.id)]).write({"stage_id": keeper.id})
            duplicate.unlink()


def _pick_stage_keeper(env, stage_name, stages):
    xmlid = CREART_STAGE_XMLID_BY_NAME.get(stage_name)
    if xmlid:
        canonical = env.ref(f"crm_extended_3D.{xmlid}", raise_if_not_found=False)
        if canonical and canonical in stages:
            return canonical
    Lead = env["crm.lead"]
    return max(stages, key=lambda s: Lead.search_count([("stage_id", "=", s.id)]))
