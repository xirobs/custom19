# -*- coding: utf-8 -*-
"""Intérprete por reglas del dictado del programa semanal (sin inteligencia artificial externa).

Convierte lo que dicta el jefe ("A las cuatro y cuarto tendremos un juego de integración por equipos durante veinte
minutos. Después…") en actividades con horario, nombre, descripción y las casillas del Programa y Método Scout que
están respaldadas por palabras del propio dictado. Nunca inventa horarios: si no se dijeron, quedan "por confirmar".
"""
import re
import unicodedata

FLAGS = ('structure', 'symbolic', 'progress', 'promise', 'groups', 'doing', 'outdoor', 'adult')
FLAG_LABELS = {
    'structure': 'Estructura', 'symbolic': 'Marco simbólico', 'progress': 'Adelanto progresivo',
    'promise': 'Compromiso (Promesa)', 'groups': 'Pequeños grupos naturales', 'doing': 'Aprender haciendo',
    'outdoor': 'Aire libre', 'adult': 'Adulto estimulante y no interferente',
}
STAGES = [('opening', 'Apertura'), ('first', 'Primera etapa'), ('second', 'Segunda etapa'), ('closing', 'Cierre')]
KINDS = ['Marco simbólico', 'Calentamiento', 'Desfogue', 'Mediano', 'Técnica', 'Reflexión', 'Ceremonia', 'Avisos']

# Palabras que respaldan cada casilla (sin acentos, minúsculas). Se buscan como palabra o frase completa.
KEYWORDS = {
    'symbolic': ['historia', 'cuento', 'lectura', 'personaje', 'ambientacion', 'ambientad', 'mistica', 'ceremonia',
                 'mowgli', 'akela', 'baloo', 'bagheera', 'kaa', 'raksha', 'tabaqui', 'hathi', 'vantha', 'shirma',
                 'selva', 'consejo de roca', 'parado de roca', 'parada', 'gran aullido', 'aullido', 'totem', 'grito',
                 'gritos', 'lema', 'cancion', 'canciones', 'sena del castor', 'saludo', 'flor de lis', 'marco simbolico',
                 'tradicion', 'simbolo', 'simbolos', 'leyenda', 'castor', 'castores del bosque', 'danza', 'fogata'],
    'structure': ['patrulla', 'patrullas', 'seisena', 'seisenas', 'madriguera', 'madrigueras', 'guia de patrulla',
                  'subguia', 'seisenero', 'seisenera', 'cargo', 'cargos', 'responsabilidad', 'responsabilidades',
                  'organizarse', 'se organizan', 'organizaran', 'distribuir las tareas', 'distribuir tareas',
                  'distribuyen', 'consejo de patrulla', 'consejo de seisena', 'asamblea', 'formacion', 'formados',
                  'roles', 'revision de uniforme'],
    'progress': ['ensenar', 'ensenaremos', 'ensenara', 'aprender', 'aprenderan', 'practica', 'practicar', 'tecnica',
                 'tecnicas', 'nudo', 'nudos', 'amarre', 'amarres', 'cabuyeria', 'primeros auxilios', 'brujula',
                 'mapa', 'croquis', 'senales', 'morse', 'semaforo', 'evaluar', 'evaluacion', 'examen', 'demostrar',
                 'especialidad', 'especialidades', 'adelanto', 'adelantos', 'insignia', 'requisitos', 'tercera clase',
                 'segunda clase', 'primera clase', 'habilidades', 'conocimientos', 'repaso', 'repasar', 'pionerismo',
                 'campismo', 'orientacion con'],
    'promise': ['ley', 'promesa', 'principios', 'oracion', 'oraciones', 'dios', 'honores', 'bandera nacional',
                'honores a la bandera', 'patria', 'compromiso', 'buena accion', 'virtudes', 'deberes para con dios'],
    'groups': ['patrulla', 'patrullas', 'seisena', 'seisenas', 'madriguera', 'madrigueras', 'por equipos',
               'en equipos', 'equipo', 'equipos', 'relevos por', 'grupos pequenos', 'cada patrulla', 'cada seisena'],
    'doing': ['construir', 'construira', 'construiran', 'construccion', 'practica', 'practicar', 'practicaran',
              'elaborar', 'elaboraran', 'armar', 'armaran', 'taller', 'experimento', 'experimentar', 'aplicar',
              'aplicaran', 'aplicando', 'realizar los nudos', 'manualidad', 'cocinar', 'preparar', 'montar'],
    'outdoor': ['aire libre', 'al exterior', 'afuera', 'parque', 'bosque', 'campo', 'cerro', 'montana', 'naturaleza',
                'rio', 'laguna', 'jardin', 'excursion', 'caminata', 'campamento', 'explanada', 'cancha', 'senderismo'],
    'adult': ['supervisando', 'supervisa', 'supervisara', 'dando orientacion', 'brindando orientacion', 'orientara',
              'acompana', 'acompanara', 'acompanando', 'facilita', 'facilitara', 'motiva', 'motivara', 'sin intervenir',
              'solo observa', 'observara', 'ellos deciden', 'deciden', 'autonomia', 'deberan organizarse',
              'se organizan solos', 'apoyara cuando sea necesario', 'cuando sea necesario'],
}
OPENING_WORDS = ['honores', 'bandera', 'inicio de junta', 'apertura', 'formacion inicial', 'oracion inicial', 'bienvenida']
CLOSING_WORDS = ['cierre', 'avisos', 'despedida', 'oracion final', 'terminaremos con', 'cerramos con', 'cerraremos con']
KIND_WORDS = [
    ('Desfogue', ['desfogue', 'correr', 'carrera']),
    ('Calentamiento', ['calentamiento', 'estiramiento']),
    ('Técnica', ['nudo', 'nudos', 'amarre', 'amarres', 'cabuyeria', 'primeros auxilios', 'brujula', 'tecnica',
                 'morse', 'semaforo', 'pionerismo']),
    ('Reflexión', ['reflexion', 'consejo de roca', 'ley y promesa', 'platica', 'charla']),
    ('Marco simbólico', ['historia', 'cuento', 'ambientacion', 'mowgli', 'mistica', 'danza']),
    ('Ceremonia', ['ceremonia', 'honores', 'promesa']),
    ('Avisos', ['avisos']),
    ('Mediano', ['juego', 'relevos', 'competencia', 'rally']),
]

NUMBERS = {
    'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9,
    'diez': 10, 'once': 11, 'doce': 12, 'quince': 15, 'veinte': 20, 'veinticinco': 25, 'treinta': 30,
    'treinta y cinco': 35, 'cuarenta': 40, 'cuarenta y cinco': 45, 'cincuenta': 50, 'cincuenta y cinco': 55,
}
MINUTE_WORDS = {'en punto': 0, 'y cuarto': 15, 'y media': 30, 'menos cuarto': -15, 'y cinco': 5, 'y diez': 10,
                'y quince': 15, 'y veinte': 20, 'y veinticinco': 25, 'y treinta': 30, 'y treinta y cinco': 35,
                'y cuarenta': 40, 'y cuarenta y cinco': 45, 'y cincuenta': 50, 'y cincuenta y cinco': 55}
HOUR_WORDS = r'(una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|\d{1,2})'
MIN_PART = (r'(?::(\d{2})|\s+(y cuarto|y media|menos cuarto|en punto|y cincuenta y cinco|y cincuenta|'
            r'y cuarenta y cinco|y cuarenta|y treinta y cinco|y treinta|y veinticinco|y veinte|y quince|y diez|y cinco))?')
TIME_RE = re.compile(r'\b(?:a|de|desde|hasta|para)\s+las?\s+' + HOUR_WORDS + MIN_PART + r'(?:\s*(?:hrs|horas|h|pm|p\.?\s?m\.?)(?![a-z]))?\b')
BARE_TIME_RE = re.compile(r'\b(\d{1,2}):(\d{2})\s*(?:hrs|horas|h|pm|p\.?\s?m\.?)(?![a-z])?')
RANGE_RE = re.compile(r'\b(?:de|desde)\s+(?:las?\s+)?' + HOUR_WORDS + MIN_PART + r'\s+(?:a|hasta)\s+(?:las?\s+)?' + HOUR_WORDS + MIN_PART)
DURATION_RE = re.compile(
    r'\b(?:durante|por|de|unos|unas)?\s*(una hora y media|hora y media|media hora|una hora|(\d{1,3}|cinco|diez|quince|'
    r'veinte|veinticinco|treinta|treinta y cinco|cuarenta|cuarenta y cinco|cincuenta|sesenta|noventa)\s+minutos?)\b')
CONNECTORS = ['despues de eso', 'despues', 'posteriormente', 'al terminar', 'luego', 'a continuacion', 'enseguida',
              'mas tarde', 'finalmente', 'para terminar', 'por ultimo', 'al final', 'y terminaremos con',
              'y terminamos con', 'y cerraremos con', 'y cerramos con']
STARTERS = ['haremos', 'tendremos', 'realizaremos', 'jugaremos', 'vamos a', 'habra', 'empezaremos', 'comenzaremos',
            'iniciaremos', 'cerraremos', 'terminaremos', 'continuaremos', 'seguiremos', 'pasaremos a', 'tendran que',
            'se hara', 'se realizara', 'sigue', 'primero', 'iniciamos', 'empezamos', 'comenzamos']
LEAD_PHRASES = [
    r'el sabado', r'el domingo', r'el viernes', r'hoy', r'primero', r'despues de eso', r'despues', r'posteriormente',
    r'al terminar', r'luego', r'a continuacion', r'enseguida', r'mas tarde', r'finalmente', r'para terminar',
    r'por ultimo', r'al final', r'y', r'comenzaremos con', r'empezaremos con', r'iniciaremos con', r'comenzaremos',
    r'empezaremos', r'iniciaremos', r'terminaremos con', r'cerraremos con', r'terminamos con', r'cerramos con',
    r'tendremos', r'haremos', r'realizaremos', r'jugaremos', r'vamos a hacer', r'vamos a', r'habra', r'sigue',
    r'continuaremos con', r'seguiremos con', r'pasaremos a', r'se hara', r'se realizara', r'(?:los|las) \w+ tendran que',
    r'tendran que', r'deberan',
]


def strip_accents(text):
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()


def norm(text):
    return re.sub(r'\s+', ' ', strip_accents(text).lower()).strip()


def _num(word):
    if word is None:
        return None
    word = word.strip()
    return int(word) if word.isdigit() else NUMBERS.get(word)


def _to_minutes(hour, minute_digits, minute_words, ref_start=None):
    h = _num(hour)
    if h is None:
        return None
    m = int(minute_digits) if minute_digits else MINUTE_WORDS.get((minute_words or '').strip(), 0)
    if m < 0:
        h, m = h - 1, 60 + m
    if h <= 12:
        # Las juntas son por la tarde: "a las cuatro" son las 16:00. Si la sesión es de mañana se respeta.
        if ref_start is not None and ref_start < 12 * 60 and 7 <= h <= 12:
            pass
        elif h < 9 or (ref_start is not None and ref_start >= 12 * 60 and h < 12):
            h += 12
    return h * 60 + m


def fmt(minutes):
    if minutes is None:
        return ''
    return '%02d:%02d' % divmod(int(minutes), 60)


def parse_hhmm(value):
    if not value:
        return None
    match = re.match(r'^\s*(\d{1,2}):(\d{2})', str(value))
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _duration(text):
    match = DURATION_RE.search(text)
    if not match:
        return None
    phrase = match.group(1)
    if 'hora y media' in phrase:
        return 90
    if 'media hora' in phrase:
        return 30
    if 'una hora' in phrase:
        return 60
    return _num(match.group(2)) or {'sesenta': 60, 'noventa': 90}.get(match.group(2))


def _has(text, word):
    return re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', text) is not None


def classify(text, location=''):
    """{casilla: palabra que la respalda} solo para lo que el texto dice."""
    low = norm(text)
    found = {}
    for flag, words in KEYWORDS.items():
        for word in words:
            if _has(low, word):
                found[flag] = word
                break
    if 'outdoor' not in found and location:
        loc = norm(location)
        for word in KEYWORDS['outdoor']:
            if _has(loc, word):
                found['outdoor'] = 'lugar: ' + location
                break
    # "guía" como verbo del adulto no cuenta como estructura; "orientación" sola no es técnica.
    return found


def _kind(low):
    for kind, words in KIND_WORDS:
        if any(_has(low, w) for w in words):
            return kind
    return ''


def _clean_sentence(text):
    text = re.sub(r'\s+', ' ', (text or '').strip(' ,.;:-'))
    if not text:
        return ''
    text = text[0].upper() + text[1:]
    return text if text.endswith(('.', '!', '?')) else text + '.'


def _strip_time_phrases(original):
    """Quita del texto original (con acentos) las menciones de hora y de duración."""
    text = original
    patterns = [
        r'\b(?:de|desde)\s+(?:las?\s+)?' + HOUR_WORDS + MIN_PART + r'\s+(?:a|hasta)\s+(?:las?\s+)?' + HOUR_WORDS + MIN_PART,
        r'\b(?:a|de|desde|hasta|para)\s+las?\s+' + HOUR_WORDS + MIN_PART + r'(?:\s*(?:hrs|horas|h|pm|p\.?\s?m\.?)(?![a-z]))?',
        r'\b\d{1,2}:\d{2}\s*(?:hrs|horas|h|pm|p\.?\s?m\.?)(?![a-z])?',
        r'\b(?:durante|por|de|unos|unas)?\s*(?:una hora y media|hora y media|media hora|una hora|(?:\d{1,3}|cinco|diez|'
        r'quince|veinte|veinticinco|treinta|treinta y cinco|cuarenta|cuarenta y cinco|cincuenta|sesenta|noventa)'
        r'\s+minutos?)',
    ]
    for pattern in patterns:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)
    accentless = strip_accents(text)
    # quita los conectores y verbos de arranque del principio
    changed = True
    while changed:
        changed = False
        stripped = accentless.strip(' ,.;:')
        for lead in LEAD_PHRASES:
            match = re.match(r'^' + lead + r'\b[\s,]*', stripped, flags=re.IGNORECASE)
            if match and match.end() > 0:
                cut = len(text.strip(' ,.;:')) - len(stripped[match.end():])
                text = text.strip(' ,.;:')[cut:]
                accentless = strip_accents(text)
                changed = True
                break
    return re.sub(r'\s+', ' ', text).strip(' ,.;:')


def _drop_articles(text):
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'^(?:con|un|una|unos|unas|el|la|los|las)\s+', '', text, flags=re.IGNORECASE)
    return text


def _without_people(text, leaders):
    """Quita "responsable X", "a cargo de X" y los nombres de dirigentes (no cuentan como marco simbólico)."""
    text = re.sub(r',?\s*(?:responsables?:?|a cargo de|lo dirige|la dirige|lo da|la da)\s+[^,.;]+', '', text,
                  flags=re.IGNORECASE)
    for name in leaders or []:
        text = re.sub(r'(?<![\w])' + re.escape(name) + r'(?![\w])', ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def _name(core):
    """Nombre corto de la actividad a partir del texto ya sin horas ni conectores."""
    text = _drop_articles(core.strip())
    text = re.split(r'\.|,|;|\s+y\s+cada\s+|\s+para\s+|\s+donde\s+|\s+en el que\s+|\s+en la que\s+|\s+utilizando\s+',
                    text, maxsplit=1)[0]
    words = text.split()
    if len(words) > 10:
        text = ' '.join(words[:10])
    text = text.strip(' .')
    return (text[:1].upper() + text[1:]) if text else ''


def _split_segments(text):
    """Separa el dictado en frases; una frase sin hora, conector ni verbo de arranque se une a la anterior."""
    pieces = [p for p in re.split(r'(?<=[.;!?])\s+|\n+', text or '') if p.strip()]
    expanded = []
    for piece in pieces:
        # "… y terminaremos con los avisos" separa una actividad dentro de la misma frase
        parts = re.split(r'(\s+y\s+(?:terminaremos|terminamos|cerraremos|cerramos)\s+con\s+)', piece, flags=re.IGNORECASE)
        current = parts[0]
        for i in range(1, len(parts), 2):
            expanded.append(current)
            current = parts[i].strip() + ' ' + parts[i + 1]
        expanded.append(current)
    segments = []
    for piece in expanded:
        low = norm(piece)
        starts = (TIME_RE.search(low) or BARE_TIME_RE.search(low) or RANGE_RE.search(low)
                  or any(low.startswith(c) or low.startswith(c.replace(' ', ', ')) for c in CONNECTORS)
                  or any(re.match(r'^(?:[\w]+\s+){0,3}' + s + r'\b', low) for s in STARTERS))
        if segments and not starts:
            segments[-1] = segments[-1].rstrip() + ' ' + piece.strip()
        else:
            segments.append(piece.strip())
    return segments


def _times(low, ref_start):
    """(inicio, fin) en minutos desde la medianoche, o None."""
    match = RANGE_RE.search(low)
    if match:
        start = _to_minutes(match.group(1), match.group(2), match.group(3), ref_start)
        end = _to_minutes(match.group(4), match.group(5), match.group(6), ref_start)
        return start, end
    start = end = None
    for m in TIME_RE.finditer(low):
        prefix = low[m.start():m.start() + 6]
        value = _to_minutes(m.group(1), m.group(2), m.group(3), ref_start)
        if prefix.startswith('hasta') and start is not None:
            end = value
        elif start is None:
            start = value
    if start is None:
        m = BARE_TIME_RE.search(low)
        if m:
            start = _to_minutes(m.group(1), m.group(2), None, ref_start)
    return start, end


def new_line(**values):
    line = {'start': '', 'end': '', 'stage': '', 'kind': '', 'name': '', 'objective': '', 'material': '',
            'responsible': '', 'description': '', 'flags': {f: False for f in FLAGS}, 'evidence': {}, 'pending': []}
    line.update(values)
    return line


def parse_dictation(text, ref_start=None, location='', leaders=None):
    """Lista de actividades a partir de un dictado continuo."""
    leaders = leaders or []
    lines = []
    for segment in _split_segments(text):
        low = norm(segment)
        start, end = _times(low, ref_start)
        duration = _duration(low)
        core = _strip_time_phrases(segment)
        if not core:
            continue
        if start is not None and end is None and duration:
            end = start + duration
        found = classify(_without_people(segment, leaders), location)
        core = re.sub(r',?\s*(?:responsables?:?|a cargo de)\s+[^,.;]+', '', core, flags=re.IGNORECASE)
        responsible = ', '.join(name for name in leaders if _has(low, norm(name)))
        material = ''
        mat = re.search(r'\b(?:material(?:es)?:?|necesitamos|necesitaremos|llevar|con material de)\s+([^.;]+)', segment,
                        flags=re.IGNORECASE)
        if mat:
            material = mat.group(1).strip(' ,.')
        stage = ''
        if any(_has(low, w) for w in OPENING_WORDS):
            stage = 'opening'
        elif any(_has(low, w) for w in CLOSING_WORDS):
            stage = 'closing'
        lines.append(new_line(
            start=fmt(start), end=fmt(end), duration=duration, connector=start is None,
            stage=stage, kind=_kind(low), name=_name(core), description=_clean_sentence(_drop_articles(core)),
            responsible=responsible, material=material,
            flags={f: f in found for f in FLAGS}, evidence=found))
    return lines


def finish(lines, ref_start=None):
    """Completa horarios que se pueden deducir (sin inventar), etapas y avisos de empalmes o pendientes."""
    previous_end = None
    for i, line in enumerate(lines):
        start = parse_hhmm(line.get('start'))
        end = parse_hhmm(line.get('end'))
        if start is None and previous_end is not None and line.get('connector'):
            start = previous_end  # "después…": empieza cuando termina la anterior
        if start is not None and end is None and line.get('duration'):
            end = start + line['duration']
        line['start'], line['end'] = fmt(start), fmt(end)
        previous_end = end
    # el fin de una actividad sin duración es el inicio de la siguiente cuando ésta tiene hora
    for i, line in enumerate(lines[:-1]):
        if line['start'] and not line['end'] and lines[i + 1].get('start'):
            line['end'] = lines[i + 1]['start']
    # segunda pasada: "después" detrás de una actividad cuyo fin se acaba de deducir
    for i, line in enumerate(lines[1:], start=1):
        if not line['start'] and line.get('connector') and lines[i - 1]['end']:
            line['start'] = lines[i - 1]['end']
            if line.get('duration') and not line['end']:
                line['end'] = fmt(parse_hhmm(line['start']) + line['duration'])
        if line['start'] and not line['end'] and i + 1 < len(lines) and lines[i + 1].get('start'):
            line['end'] = lines[i + 1]['start']
    middle = [l for l in lines if l.get('stage') not in ('opening', 'closing')]
    half = (len(middle) + 1) // 2
    for idx, line in enumerate(middle):
        if not line.get('stage'):
            line['stage'] = 'first' if idx < half else 'second'
    warnings = []
    for i, line in enumerate(lines):
        pending = [p for p in line.get('pending', []) if p not in ('Hora de inicio por confirmar',
                                                                    'Hora de término por confirmar')]
        if not line['start']:
            pending.append('Hora de inicio por confirmar')
        if not line['end']:
            pending.append('Hora de término por confirmar')
        line['pending'] = pending
        if i and line['start'] and lines[i - 1]['end'] and parse_hhmm(line['start']) < parse_hhmm(lines[i - 1]['end']):
            warnings.append('"%s" empieza (%s) antes de que termine "%s" (%s): corrige los horarios.'
                            % (line['name'], line['start'], lines[i - 1]['name'], lines[i - 1]['end']))
        line.pop('connector', None)
        line.pop('duration', None)
    return lines, warnings


# ---------------------------------------------------------------- correcciones por voz
COMMAND_RE = {
    'replace': re.compile(r'^(?:cambia|cambiar|reemplaza|sustituye)\s+(?:la\s+)?(?:actividad\s+)?(?:de\s+)?(.+?)\s+por\s+(.+)$'),
    'delete': re.compile(r'^(?:elimina|quita|borra|eliminar|quitar|borrar)\s+(?:la\s+)?(?:actividad\s+)?(?:de\s+)?(.+)$'),
    'move': re.compile(r'^(?:mueve|pasa|recorre)\s+(?:la\s+)?(?:actividad\s+)?(?:de\s+)?(.+?)\s+(?:a|para)\s+(las?\s+.+)$'),
    'add': re.compile(r'^(?:agrega|anade|añade|agregar|anadir)\s+(?:una\s+actividad\s+)?(.+)$'),
}


def _find_line(lines, ref, ref_start):
    """Ubica la actividad por su hora ("las cuatro y media") o por su nombre."""
    low = norm(ref)
    start, _end = _times('a ' + low if low.startswith('las') else low, ref_start)
    if start is not None:
        for i, line in enumerate(lines):
            if parse_hhmm(line.get('start')) == start:
                return i
    words = [w for w in re.findall(r'[a-z]{4,}', low) if w not in ('actividad', 'juego', 'para')]
    best, score = None, 0
    for i, line in enumerate(lines):
        hay = norm(line.get('name', '') + ' ' + line.get('description', ''))
        s = sum(1 for w in words if w in hay)
        if s > score:
            best, score = i, s
    return best


def apply_command(lines, text, ref_start=None, location='', leaders=None):
    """Aplica una corrección dictada. Devuelve (líneas, mensaje) o (None, None) si no es un comando."""
    low = norm(text).strip(' .')
    for op, regex in COMMAND_RE.items():
        match = regex.match(low)
        if not match:
            continue
        if op == 'add':
            extra, extra_note = match.group(1), None
            target = None
            m2 = re.match(r'^(?:a|en)\s+(?:la\s+)?(?:actividad\s+)?(?:de\s+)?(.+?)\s+que\s+(.+)$', extra)
            if m2:
                target = _find_line(lines, m2.group(1), ref_start)
                extra_note = m2.group(2)
            if target is not None and extra_note:
                line = lines[target]
                note = _restore(text, extra_note)
                line['description'] = _clean_sentence(line['description'].rstrip('.') + '. ' + note[:1].upper() + note[1:])
                _reclassify(line, location)
                return lines, 'Se agregó a "%s": %s' % (line['name'], extra_note)
            new = parse_dictation(_restore(text, match.group(1)), ref_start, location, leaders)
            lines.extend(new)
            lines.sort(key=lambda l: parse_hhmm(l.get('start')) if l.get('start') else 10 ** 6)
            return lines, 'Se agregaron %s actividad(es).' % len(new)
        target = _find_line(lines, match.group(1), ref_start)
        if target is None:
            return lines, 'No encontré la actividad "%s"; no se cambió nada.' % match.group(1)
        line = lines[target]
        if op == 'delete':
            lines.pop(target)
            return lines, 'Se eliminó "%s".' % line['name']
        if op == 'move':
            start, _end = _times('a ' + match.group(2), ref_start)
            if start is None:
                return lines, 'No entendí la nueva hora.'
            length = (parse_hhmm(line['end']) - parse_hhmm(line['start'])) if line['end'] and line['start'] else None
            line['start'] = fmt(start)
            line['end'] = fmt(start + length) if length else ''
            lines.sort(key=lambda l: parse_hhmm(l.get('start')) if l.get('start') else 10 ** 6)
            return lines, 'Se movió "%s" a las %s.' % (line['name'], line['start'])
        # replace: "… por una práctica de primeros auxilios y agrega que será mediante equipos"
        replacement = match.group(2)
        extra = None
        parts = re.split(r'\s+y\s+(?:agrega|anade|añade)\s+que\s+', replacement, maxsplit=1)
        if len(parts) == 2:
            replacement, extra = parts
        replacement_original = _restore(text, replacement)
        core = _strip_time_phrases(replacement_original)
        extra = _restore(text, extra) if extra else None
        line['name'] = _name(core)
        line['description'] = _clean_sentence(_drop_articles(core) + ('. ' + extra[:1].upper() + extra[1:] if extra else ''))
        line['kind'] = _kind(norm(line['description']))
        _reclassify(line, location)
        return lines, 'Se cambió la actividad de las %s por "%s".' % (line['start'] or '?', line['name'])
    return None, None


def _restore(original, fragment_norm):
    """Recupera el fragmento con sus acentos desde el texto original."""
    plain = strip_accents(original).lower()
    idx = plain.find(fragment_norm[:20])
    if idx < 0:
        return fragment_norm
    return original[idx:idx + len(fragment_norm)] if len(original) >= idx + len(fragment_norm) else original[idx:]


def _reclassify(line, location, leaders=None):
    found = classify(_without_people(line['name'] + '. ' + line['description'], leaders), location)
    line['flags'] = {f: f in found for f in FLAGS}
    line['evidence'] = found


def interpret(text, lines=None, ref_start=None, location='', leaders=None):
    """Punto de entrada: dictado nuevo o corrección sobre las actividades actuales."""
    lines = [dict(l) for l in (lines or [])]
    messages = []
    for sentence in [s for s in re.split(r'(?<=[.;])\s+(?=(?:cambia|elimina|quita|borra|mueve|agrega|añade|anade)\b)',
                                         text.strip(), flags=re.IGNORECASE) if s.strip()]:
        result, message = apply_command(lines, sentence, ref_start, location, leaders)
        if result is not None:
            lines = result
            messages.append(message)
            continue
        new = parse_dictation(sentence, ref_start, location, leaders)
        # evita duplicar: si ya hay una actividad a la misma hora, se actualiza en lugar de repetirla
        for item in new:
            same = next((i for i, l in enumerate(lines) if item['start'] and l.get('start') == item['start']), None)
            if same is not None:
                lines[same].update({k: item[k] for k in ('name', 'description', 'kind', 'flags', 'evidence', 'end')
                                    if item.get(k)})
                messages.append('Se actualizó la actividad de las %s.' % item['start'])
            else:
                lines.append(item)
        if new:
            messages.append('Se interpretaron %s actividad(es).' % len(new))
    lines, warnings = finish(lines, ref_start)
    if all(l.get('start') for l in lines):
        lines.sort(key=lambda l: parse_hhmm(l['start']))
    return {'lines': lines, 'messages': messages, 'warnings': warnings}
