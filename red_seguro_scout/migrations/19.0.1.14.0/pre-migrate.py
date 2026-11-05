# -*- coding: utf-8 -*-
"""Secciones de la Red: Castores, Manada (Lobatos y Gacelas), Tropa, Clan y Jefatura y Adultos.
«Comunidad» deja de existir (sus integrantes pasan a Tropa) y Manada menor de 6 años pasa a Castores."""


def migrate(cr, version):
    cr.execute("UPDATE rss_insured SET section = 'tropa' WHERE section = 'comunidad'")
    cr.execute("""UPDATE rss_insured SET section = 'castores'
                   WHERE section = 'manada' AND birthdate IS NOT NULL
                     AND birthdate > (CURRENT_DATE - INTERVAL '6 years')""")
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'rss_report_line' AND column_name = 'section'")
    if cr.fetchone():
        cr.execute("UPDATE rss_report_line SET section = 'tropa' WHERE section = 'comunidad'")
