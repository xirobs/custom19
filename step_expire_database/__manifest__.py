# Copyright (C) 2026 - TODAY, jamie.escalante7@gmail.com
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
# -*- coding: utf-8 -*-
{
    "name": "Step - Remove Database Expiration Message",
    "version": "19.0.1.0.0",
    "summary": "Remueve el panel que muestra que la base de datos expirará",
    "description": "Oculta o elimina el div #database_expiration_panel en la pantalla de login y home menu para no mostrar el aviso de expiración de base de datos.",
    "category": "Hidden",
    "author": "jamie.escalante7@gmail.com",
    "license": "LGPL-3",
    "depends": [
        "web",
        "web_enterprise"
    ],
    "data": [
        # No view templates to avoid xpath/parse errors; assets are declared below
    ],
    "images": ["static/description/icon.png"],
    "assets": {
        "web.assets_frontend": [
            "step_expire_database/static/src/js/remove_expire_msg.js",
            "step_expire_database/static/src/scss/hide_expiration.scss"
        ],
        "web.assets_backend": [
            "step_expire_database/static/src/js/remove_expire_msg.js",
            "step_expire_database/static/src/scss/hide_expiration.scss"
        ]
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "post_init_hook": "post_init_delete_cron",
}
