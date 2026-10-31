# -*- coding: utf-8 -*-
"""Turnos de palabra en las videollamadas de Conversaciones (cualquier canal)."""
from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

ERRORS = (AccessError, MissingError, UserError)


class ScoutCallTurns(http.Controller):

    @staticmethod
    def _channel(channel_id):
        channel = request.env['discuss.channel'].sudo().browse(int(channel_id or 0)).exists()
        if not channel:
            raise MissingError(_('La llamada no existe.'))
        return channel

    @staticmethod
    def _is_participant(channel):
        user = request.env.user
        if not user._is_public():
            return bool(channel.channel_member_ids.filtered(lambda m: m.partner_id == user.partner_id)) \
                or channel.with_user(user)._scout_turn_can_moderate()
        guest = request.env['mail.guest']._get_guest_from_context()
        return bool(guest and channel.channel_member_ids.filtered(lambda m: m.guest_id == guest))

    @http.route(['/scouts/turnos/api/estado'], type='jsonrpc', auth='public')
    def turns_state(self, channel_id, **kw):
        try:
            channel = self._channel(channel_id)
        except ERRORS:
            return {}
        # Las llamadas de consejo tienen su propio recuadro (orden del día, acta)
        if request.env['scout.meeting']._scout_for_channel(channel.id):
            return {'council': True}
        if not self._is_participant(channel):
            return {}
        payload = channel.with_user(request.env.user)._scout_turn_payload() if not request.env.user._is_public() \
            else channel._scout_turn_payload()
        if request.env.user._is_public():
            payload['can_moderate'] = False
        return payload

    @http.route(['/scouts/turnos/api/control'], type='jsonrpc', auth='user')
    def turns_control(self, channel_id, op, member_id=None, minutes=None, cfg=None, **kw):
        try:
            channel = self._channel(channel_id).with_user(request.env.user)
            return channel._scout_turn_control(op, member_id=member_id, minutes=minutes, cfg=cfg)
        except ERRORS as e:
            return {'error': str(e.args[0] if e.args else e)}

    @http.route(['/scouts/turnos/api/revisar'], type='jsonrpc', auth='public')
    def turns_check(self, channel_id, muted_key=None, **kw):
        """Algún participante vio que cambió el color o se acabó el tiempo: el servidor manda los avisos (una vez)."""
        try:
            channel = self._channel(channel_id)
        except ERRORS:
            return {}
        if not self._is_participant(channel):
            return {}
        if muted_key:
            channel._scout_turn_muted(muted_key)
        channel._scout_turn_check()
        return {'ok': True}
