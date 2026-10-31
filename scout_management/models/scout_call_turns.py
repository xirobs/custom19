# -*- coding: utf-8 -*-
"""Turnos de palabra en CUALQUIER videollamada de Conversaciones (Discuss).

* La configuración por omisión está en la empresa (Datos del grupo) y cada canal puede cambiarla.
* Cada participante (discuss.channel.member) puede tener su propia duración de discurso.
* Semáforo: verde con el tiempo completo, amarillo al pasar el % configurado, rojo cuando quedan los segundos
  configurados; en los últimos segundos suena una alarma y al terminar se silencia el micrófono (si está activado).
* Avisos: mensaje en el chat de la llamada y notificación emergente en la pantalla de quien habla, enviados por el
  bus de Odoo (bus.bus). El servidor revisa los tiempos con una acción planificada (ir.cron) y también cuando algún
  participante detecta el cambio, para que el aviso salga al segundo y no se repita.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

TURN_STATES = [('idle', 'Nadie tiene la palabra'), ('running', 'Hablando'), ('paused', 'En pausa'),
               ('over', 'Tiempo agotado')]


class ResCompanyCallTurns(models.Model):
    _inherit = 'res.company'

    scout_turn_minutes = fields.Float(string='Duración por turno (min)', default=3.0)
    scout_turn_yellow_pct = fields.Integer(string='Amarillo al pasar (% del tiempo)', default=50)
    scout_turn_red_seconds = fields.Integer(string='Rojo cuando quedan (s)', default=60)
    scout_turn_alarm_seconds = fields.Integer(string='Alarma en los últimos (s)', default=10)
    scout_turn_auto_mute = fields.Boolean(string='Silenciar al terminar el tiempo', default=True)
    scout_turn_grace = fields.Integer(string='Segundos de gracia antes de silenciar', default=5)
    scout_turn_chat = fields.Boolean(string='Avisos en el chat de la llamada', default=True)
    scout_turn_popup = fields.Boolean(string='Aviso emergente en la pantalla de quien habla', default=True)


class DiscussChannelMemberTurns(models.Model):
    _inherit = 'discuss.channel.member'

    scout_speech_seconds = fields.Integer(string='Duración del discurso (s)',
                                          help='Tiempo de palabra de este participante en el canal. 0 = el del canal.')


class DiscussChannelTurns(models.Model):
    _inherit = 'discuss.channel'

    # --- configuración (se copia de la empresa al activar y se puede cambiar por canal)
    scout_turns_enabled = fields.Boolean(string='Turnos de palabra', copy=False)
    scout_turn_minutes = fields.Float(string='Duración por turno (min)', default=3.0)
    scout_turn_yellow_pct = fields.Integer(string='Amarillo al pasar (%)', default=50)
    scout_turn_red_seconds = fields.Integer(string='Rojo cuando quedan (s)', default=60)
    scout_turn_alarm_seconds = fields.Integer(string='Alarma en los últimos (s)', default=10)
    scout_turn_auto_mute = fields.Boolean(string='Silenciar al terminar', default=True)
    scout_turn_grace = fields.Integer(string='Gracia antes de silenciar (s)', default=5)
    scout_turn_chat = fields.Boolean(string='Avisos en el chat', default=True)
    scout_turn_popup = fields.Boolean(string='Aviso emergente', default=True)
    scout_turn_moderator_ids = fields.Many2many('res.users', 'scout_channel_turn_moderator_rel', 'channel_id', 'user_id',
                                                string='Moderan los turnos',
                                                help='Además de quien creó el canal y la jefatura de grupo.')
    # --- estado del turno en curso
    scout_turn_member_id = fields.Many2one('discuss.channel.member', string='Tiene la palabra', copy=False)
    scout_turn_name = fields.Char(copy=False)
    scout_turn_state = fields.Selection(TURN_STATES, default='idle', copy=False)
    scout_turn_started = fields.Datetime(copy=False)
    scout_turn_elapsed = fields.Float(copy=False)
    scout_turn_seconds = fields.Integer(copy=False)
    scout_turn_key = fields.Integer(copy=False)
    scout_turn_flags = fields.Char(copy=False, help='Avisos ya enviados en este turno: half,red,alarm,over,muted')
    scout_turn_log_ids = fields.One2many('scout.call.turn', 'channel_id', string='Historial de turnos')

    # ------------------------------------------------------------------ permisos
    def _scout_turn_can_moderate(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if user._is_public():
            return False
        channel = self.sudo()
        if channel.create_uid == user or user in channel.scout_turn_moderator_ids:
            return True
        if not user.share and user.has_group('scout_management.group_scout_officer'):
            return True
        return bool(user.partner_id and user._scout_leader_sections().filtered('is_leadership'))

    def _scout_turn_check_access(self, moderate=False):
        self.ensure_one()
        channel = self.sudo()
        if moderate and not self._scout_turn_can_moderate():
            raise AccessError(_('Solo quien modera la llamada puede dar o quitar la palabra.'))
        if not moderate and not channel.channel_member_ids.filtered(
                lambda m: m.partner_id == self.env.user.partner_id) and not self._scout_turn_can_moderate():
            raise AccessError(_('No participas en esta llamada.'))
        return channel

    # ------------------------------------------------------------------ tiempo
    def _scout_turn_elapsed_now(self, now=None):
        self.ensure_one()
        now = now or fields.Datetime.now()
        elapsed = self.scout_turn_elapsed or 0.0
        if self.scout_turn_state in ('running', 'over') and self.scout_turn_started:
            elapsed += max(0.0, (now - self.scout_turn_started).total_seconds())
        return elapsed

    def _scout_turn_phase(self, elapsed=None):
        self.ensure_one()
        if self.scout_turn_state not in ('running', 'paused', 'over') or not self.scout_turn_seconds:
            return 'idle'
        elapsed = self._scout_turn_elapsed_now() if elapsed is None else elapsed
        remaining = self.scout_turn_seconds - elapsed
        if remaining <= 0:
            return 'over'
        if remaining <= (self.scout_turn_red_seconds or 0):
            return 'red'
        if elapsed >= self.scout_turn_seconds * (self.scout_turn_yellow_pct or 50) / 100.0:
            return 'yellow'
        return 'green'

    def _scout_turn_config(self):
        self.ensure_one()
        return {'yellow_pct': self.scout_turn_yellow_pct or 50, 'red_seconds': self.scout_turn_red_seconds or 0,
                'alarm_seconds': self.scout_turn_alarm_seconds or 0, 'auto_mute': self.scout_turn_auto_mute,
                'grace': max(0, self.scout_turn_grace or 0), 'chat': self.scout_turn_chat,
                'popup': self.scout_turn_popup, 'minutes': self.scout_turn_minutes or 3.0}

    def _scout_turn_payload(self):
        self.ensure_one()
        channel = self.sudo()
        members = channel.channel_member_ids
        payload = {
            'channel_id': channel.id, 'enabled': channel.scout_turns_enabled,
            'can_moderate': self._scout_turn_can_moderate(), 'state': channel.scout_turn_state,
            'running': channel.scout_turn_state in ('running', 'over'), 'config': channel._scout_turn_config(),
            'turn': None,
            'members': [{'id': m.id, 'partner_id': m.partner_id.id or False, 'guest_id': m.guest_id.id or False,
                         'name': m.partner_id.name or m.guest_id.name or '',
                         'seconds': m.scout_speech_seconds or 0} for m in members],
        }
        if channel.scout_turn_state in ('running', 'paused', 'over'):
            member = channel.scout_turn_member_id
            payload['turn'] = {
                'key': channel.scout_turn_key, 'member_id': member.id or False,
                'partner_id': member.partner_id.id or False, 'guest_id': member.guest_id.id or False,
                'speaker': channel.scout_turn_name or member.partner_id.name or member.guest_id.name or '',
                'duration': channel.scout_turn_seconds, 'elapsed': round(channel._scout_turn_elapsed_now(), 1),
                'phase': channel._scout_turn_phase(),
            }
        return payload

    def _scout_turn_broadcast(self):
        for channel in self:
            try:
                channel.sudo()._bus_send('scout.turn/update', {'channel_id': channel.id})
            except Exception:  # noqa: BLE001
                _logger.info('No se pudo avisar el cambio de turno por el bus', exc_info=True)

    def _scout_turn_chat(self, text):
        channel = self.sudo()
        if not channel.scout_turn_chat:
            return
        try:
            channel.message_post(body=text, message_type='comment', subtype_xmlid='mail.mt_comment',
                                 author_id=self.env.ref('base.partner_root').id)
        except Exception:  # noqa: BLE001
            _logger.info('No se pudo avisar en el chat', exc_info=True)

    def _scout_turn_alert_speaker(self, kind, text):
        """Notificación emergente en la pantalla de quien habla (y orden de silenciar, si aplica)."""
        channel = self.sudo()
        member = channel.scout_turn_member_id
        if not member:
            return
        try:
            member._bus_send('scout.turn/alert', {
                'channel_id': channel.id, 'key': channel.scout_turn_key, 'kind': kind, 'message': text,
                'popup': channel.scout_turn_popup,
                'mute': kind == 'over' and channel.scout_turn_auto_mute, 'grace': max(0, channel.scout_turn_grace or 0),
            })
        except Exception:  # noqa: BLE001
            _logger.info('No se pudo mandar el aviso a quien habla', exc_info=True)

    # ------------------------------------------------------------------ revisión de tiempos (cron y participantes)
    def _scout_turn_check(self):
        """Manda una sola vez cada aviso del turno en curso según el tiempo que lleva."""
        for channel in self.sudo().filtered(lambda c: c.scout_turns_enabled and c.scout_turn_state in ('running',
                                                                                                         'over')):
            now = fields.Datetime.now()
            elapsed = channel._scout_turn_elapsed_now(now)
            phase = channel._scout_turn_phase(elapsed)
            flags = set(filter(None, (channel.scout_turn_flags or '').split(',')))
            who = channel.scout_turn_name or _('quien tiene la palabra')
            remaining = int(round(channel.scout_turn_seconds - elapsed))
            new = set()
            if phase in ('yellow', 'red', 'over') and 'half' not in flags:
                new.add('half')
                channel._scout_turn_alert_speaker('half', _('Llevas la mitad de tu tiempo.'))
            if phase in ('red', 'over') and 'red' not in flags:
                new.add('red')
                channel._scout_turn_chat(_('⏳ A %(who)s le queda %(t)s.', who=who,
                                           t=_('1 minuto') if remaining >= 55 else _('%s segundos', max(remaining, 0))))
                channel._scout_turn_alert_speaker('red', _('Tu tiempo se está acabando: te quedan %s s.',
                                                           max(remaining, 0)))
            if phase == 'over' and 'over' not in flags:
                new.add('over')
                mute = _(' Su micrófono se silenciará en %s s.', channel.scout_turn_grace or 0) \
                    if channel.scout_turn_auto_mute else ''
                channel._scout_turn_chat(_('⏰ Terminó el tiempo de %(who)s.%(mute)s', who=who, mute=mute))
                channel._scout_turn_alert_speaker('over', _('Tu tiempo terminó.') + mute)
                channel.scout_turn_state = 'over'
            if new:
                channel.scout_turn_flags = ','.join(sorted(flags | new))
                channel._scout_turn_broadcast()

    @api.model
    def _cron_scout_turns(self):
        self.search([('scout_turns_enabled', '=', True), ('scout_turn_state', 'in', ('running', 'over'))]) \
            ._scout_turn_check()

    # ------------------------------------------------------------------ controles
    def _scout_turn_close(self, now):
        """Guarda el turno que termina en el historial."""
        for channel in self.filtered(lambda c: c.scout_turn_state in ('running', 'paused', 'over')):
            seconds = int(round(channel._scout_turn_elapsed_now(now)))
            if seconds >= 2:
                self.env['scout.call.turn'].sudo().create({
                    'channel_id': channel.id, 'member_id': channel.scout_turn_member_id.id or False,
                    'name': channel.scout_turn_name, 'planned_seconds': channel.scout_turn_seconds,
                    'seconds': seconds, 'start': now - timedelta(seconds=seconds)})

    def _scout_turn_control(self, op, member_id=None, minutes=None, cfg=None):
        channel = self._scout_turn_check_access(moderate=True)
        now = fields.Datetime.now()
        if op == 'enable':
            company = self.env.company
            channel.write({
                'scout_turns_enabled': True, 'scout_turn_minutes': company.scout_turn_minutes or 3.0,
                'scout_turn_yellow_pct': company.scout_turn_yellow_pct, 'scout_turn_red_seconds':
                    company.scout_turn_red_seconds, 'scout_turn_alarm_seconds': company.scout_turn_alarm_seconds,
                'scout_turn_auto_mute': company.scout_turn_auto_mute, 'scout_turn_grace': company.scout_turn_grace,
                'scout_turn_chat': company.scout_turn_chat, 'scout_turn_popup': company.scout_turn_popup,
            } if not channel.scout_turns_enabled else {'scout_turns_enabled': True})
        elif op == 'disable':
            channel._scout_turn_close(now)
            channel.write({'scout_turns_enabled': False, 'scout_turn_state': 'idle', 'scout_turn_member_id': False})
        elif op == 'floor':
            member = channel.channel_member_ids.filtered(lambda m: m.id == int(member_id or 0))[:1]
            if not member:
                raise UserError(_('Esa persona no está en la llamada.'))
            if minutes not in (None, '', False):
                seconds = int(max(0.25, float(minutes)) * 60)
            else:
                seconds = member.scout_speech_seconds or int((channel.scout_turn_minutes or 3.0) * 60)
            channel._scout_turn_close(now)
            name = member.partner_id.name or member.guest_id.name or ''
            channel.write({'scout_turn_member_id': member.id, 'scout_turn_name': name, 'scout_turn_state': 'running',
                           'scout_turn_started': now, 'scout_turn_elapsed': 0.0, 'scout_turn_seconds': seconds,
                           'scout_turn_key': (channel.scout_turn_key or 0) + 1, 'scout_turn_flags': False})
            channel._scout_turn_chat(_('🎤 Tiene la palabra: %(who)s · %(min)s min', who=name, min='%g' % (seconds / 60)))
        elif op == 'pause' and channel.scout_turn_state in ('running', 'over'):
            channel.write({'scout_turn_state': 'paused', 'scout_turn_elapsed': channel._scout_turn_elapsed_now(now),
                           'scout_turn_started': now})
        elif op == 'resume' and channel.scout_turn_state == 'paused':
            channel.write({'scout_turn_state': 'running', 'scout_turn_started': now})
        elif op == 'add' and channel.scout_turn_state in ('running', 'paused', 'over'):
            extra = int(float(minutes or 1) * 60)
            flags = set(filter(None, (channel.scout_turn_flags or '').split(',')))
            channel.write({'scout_turn_seconds': channel.scout_turn_seconds + extra,
                           'scout_turn_state': 'running' if channel.scout_turn_state == 'over' else
                           channel.scout_turn_state, 'scout_turn_flags': ','.join(flags - {'red', 'over', 'muted'})})
            channel._scout_turn_chat(_('➕ %(who)s tiene %(m)s min más.', who=channel.scout_turn_name or '',
                                       m='%g' % (extra / 60)))
        elif op == 'end':
            channel._scout_turn_close(now)
            channel.write({'scout_turn_state': 'idle', 'scout_turn_member_id': False, 'scout_turn_name': False,
                           'scout_turn_elapsed': 0.0, 'scout_turn_started': False})
        elif op == 'member_time':
            member = channel.channel_member_ids.filtered(lambda m: m.id == int(member_id or 0))[:1]
            if member:
                member.scout_speech_seconds = int(max(0.0, float(minutes or 0)) * 60)
        elif op == 'settings':
            cfg = cfg or {}
            vals = {}
            for key, field, cast in (('minutes', 'scout_turn_minutes', float), ('yellow_pct', 'scout_turn_yellow_pct', int),
                                     ('red_seconds', 'scout_turn_red_seconds', int),
                                     ('alarm_seconds', 'scout_turn_alarm_seconds', int),
                                     ('auto_mute', 'scout_turn_auto_mute', bool), ('grace', 'scout_turn_grace', int),
                                     ('chat', 'scout_turn_chat', bool), ('popup', 'scout_turn_popup', bool)):
                if key in cfg:
                    try:
                        vals[field] = cast(cfg[key])
                    except (TypeError, ValueError):
                        continue
            if 'scout_turn_yellow_pct' in vals:
                vals['scout_turn_yellow_pct'] = max(1, min(99, vals['scout_turn_yellow_pct']))
            channel.write(vals)
        channel._scout_turn_broadcast()
        return channel._scout_turn_payload()

    def _scout_turn_muted(self, key):
        """El cliente de quien habla avisa que ya se silenció (para no repetir y dejar constancia)."""
        channel = self.sudo()
        flags = set(filter(None, (channel.scout_turn_flags or '').split(',')))
        if channel.scout_turn_key == int(key or 0) and 'muted' not in flags:
            channel.scout_turn_flags = ','.join(sorted(flags | {'muted'}))
            channel._scout_turn_chat(_('🔇 Se silenció el micrófono de %s.', channel.scout_turn_name or ''))
        return True


class ScoutCallTurn(models.Model):
    _name = 'scout.call.turn'
    _description = 'Turno de palabra en una videollamada'
    _order = 'start desc, id desc'

    channel_id = fields.Many2one('discuss.channel', required=True, ondelete='cascade', index=True)
    member_id = fields.Many2one('discuss.channel.member', ondelete='set null')
    name = fields.Char(string='Habló')
    start = fields.Datetime(string='Inicio')
    planned_seconds = fields.Integer(string='Tenía (s)')
    seconds = fields.Integer(string='Usó (s)')
    over = fields.Boolean(string='Se pasó', compute='_compute_over', store=True)

    @api.depends('seconds', 'planned_seconds')
    def _compute_over(self):
        for rec in self:
            rec.over = (rec.seconds or 0) > (rec.planned_seconds or 0)
