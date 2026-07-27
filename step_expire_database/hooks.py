# Copyright (C) 2026 - TODAY, jamie.escalante7@gmail.com
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
# -*- coding: utf-8 -*-

def post_init_delete_cron(env):
    try:
        env.cr.execute("DELETE FROM ir_cron WHERE id = %s", (4,))
        env.cr.commit()
    except Exception as e:
        print(f"Error deleting ir.cron record with id=4: {e}")