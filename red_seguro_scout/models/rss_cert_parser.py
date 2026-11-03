# -*- coding: utf-8 -*-
"""Lectura de los datos de un certificado de póliza (formato «AP Colectivo» de Pan-American
México y similares, con etiquetas y renglones de tabla).

Devuelve, para el texto de una página:
  póliza, certificado, aseguradora, producto, fechas de alta / fin / vigencia,
  contratante (nombre o razón social, RFC, domicilio),
  asegurado (nombre como viene, RFC, fecha de nacimiento, sexo, domicilio),
  coberturas (cobertura, suma asegurada, deducible, periodo de espera).

Es tolerante: si una etiqueta no aparece, ese dato queda vacío.
"""
import re
import unicodedata
from datetime import date

RFC_RE = re.compile(r'^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$')
RFC_IN_RE = re.compile(r'(?<![A-Z0-9])([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})(?![A-Z0-9])')
DATE_RE = re.compile(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})')
MONEY_RE = re.compile(r'^\$\s*[\d,]+(?:\.\d+)?$')
MONEY_IN_RE = re.compile(r'(\$\s*[\d,]+(?:\.\d+)?|NO\s+APLICA|AMPARADA|INCLUIDA|N/A)', re.I)
POLICY_RE = re.compile(r'^[A-Z]{0,5}-?\d{5,}[A-Z0-9-]*$')
POLICY_CLEAN_RE = re.compile(r'^([A-Z]{0,5}-?\d{5,}(?:-\d+)*)')
CERT_RE = re.compile(r'^[A-Z]{0,4}-?\d{1,12}$')
SEX_WORDS = {'MASCULINO': 'male', 'FEMENINO': 'female', 'HOMBRE': 'male', 'MUJER': 'female'}

CONTRACTOR_LABELS = {'NOMBRE O RAZON SOCIAL', 'RAZON SOCIAL', 'NOMBRE', 'RFC', 'DOMICILIO', 'CONTRATANTE'}
INSURED_LABELS = {'NOMBRE', 'RFC', 'DOMICILIO', 'FECHA DE NACIMIENTO', 'ASEGURADO', 'SEXO'}
INSURED_END = ('OTORGO', 'COBERTURAS', 'COBERTURA', 'BENEFICIARIO', 'SOLICITO')


def _plain(value):
    """Mayúsculas sin acentos, para comparar etiquetas."""
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).upper()
    return ' '.join(value.replace(':', ' ').split())


def _to_date(text):
    match = DATE_RE.search(text or '')
    if not match:
        return False
    d, m, y = (int(x) for x in match.groups())
    try:
        return date(y, m, d)
    except ValueError:
        return False


def _money(text):
    """'$ 50,000.00' -> 50000.0; 'No aplica' -> 0.0 (y se conserva el texto aparte)."""
    digits = re.sub(r'[^\d.]', '', text or '')
    try:
        return float(digits) if digits else 0.0
    except ValueError:
        return 0.0


LABELS = sorted({
    'NOMBRE O RAZON SOCIAL', 'RAZON SOCIAL', 'NOMBRE', 'RFC', 'DOMICILIO', 'FECHA DE NACIMIENTO', 'SEXO',
    'POLIZA', 'CERTIFICADO', 'CONTRATANTE', 'ASEGURADO', 'COBERTURA', 'COBERTURAS',
    'SUMA ASEGURADA O', 'SUMA ASEGURADA', 'REGLA PARA', 'DETERMINARLA', 'DEDUCIBLE',
    'PERIODO DE ESPERA', 'PERIODO DE', 'ESPERA', 'NACIONAL/INTERNACIONAL', 'MONEDA NACIONAL/DOLARES',
}, key=len, reverse=True)


def _split_labels(piece):
    """'Nombre RFC' -> ['Nombre', 'RFC'] cuando la celda son solo etiquetas pegadas;
    'AP30101716 392' -> ['AP30101716', '392'] cuando son solo códigos."""
    words = piece.split()
    if len(words) > 1 and all(re.search(r'\d', w) for w in words) and not DATE_RE.search(piece) \
            and not piece.strip().startswith('$'):
        return words
    plain_words = _plain(piece).split()
    out, i = [], 0
    while i < len(plain_words):
        for label in LABELS:
            lw = label.split()
            if plain_words[i:i + len(lw)] == lw:
                out.append(' '.join(words[i:i + len(lw)]))
                i += len(lw)
                break
        else:
            return [piece]
    return out if len(out) > 1 else [piece]


def tokens_from_text(text):
    """Celdas del texto en orden de lectura.

    Sirve para texto «layout» (columnas separadas por 2+ espacios) y para texto plano
    (una celda por renglón). Además separa importes y RFC que vienen pegados a otro texto.
    """
    tokens = []
    for line in (text or '').splitlines():
        for cell in re.split(r'\s{2,}|\t', line.strip()):
            cell = cell.strip()
            if not cell:
                continue
            pieces = [p.strip() for p in MONEY_IN_RE.split(cell) if p and p.strip()]
            for piece in pieces:
                upper = piece.upper()
                rfc = RFC_IN_RE.search(upper)
                if rfc and upper != rfc.group(1):
                    before, after = piece[:rfc.start()].strip(), piece[rfc.end():].strip()
                    tokens.extend(x for x in (before, piece[rfc.start():rfc.end()], after) if x)
                else:
                    tokens.extend(_split_labels(piece))
    return tokens


def _find(plain_tokens, label, start=0, end=None):
    end = len(plain_tokens) if end is None else end
    for i in range(start, end):
        if plain_tokens[i] == label:
            return i
    return -1


def parse_certificate(text):
    """Datos del certificado contenidos en el texto de una página (dict; vacío si no hay)."""
    tokens = tokens_from_text(text)
    plain = [_plain(t) for t in tokens]
    joined = ' '.join(plain)
    data = {}
    if not tokens:
        return data

    # Aseguradora y producto
    if 'PAN AMERICAN' in joined or 'PAN-AMERICAN' in joined:
        data['insurer'] = 'Pan-American México'
    for product in ('AP COLECTIVO', 'ACCIDENTES PERSONALES COLECTIVO', 'ACCIDENTES PERSONALES'):
        if product in plain or (' %s ' % product) in (' %s ' % joined):
            data['product'] = product.title().replace('Ap ', 'AP ')
            break

    # Póliza y certificado: etiquetas POLIZA / CERTIFICADO y sus valores debajo o al lado
    i_pol = _find(plain, 'POLIZA')
    i_cert = _find(plain, 'CERTIFICADO')
    if i_pol < 0:
        i_pol = next((i for i, t in enumerate(plain) if t.startswith('POLIZA ') and len(t) < 30), -1)
    if i_pol >= 0:
        after = range(i_pol + 1, min(i_pol + 6, len(tokens)))
        values = [tokens[i].strip() for i in after if plain[i] not in ('CERTIFICADO', 'POLIZA')]
        inline = re.match(r'^POLIZA\s+(?:NO\.?|NUM\.?|NUMERO)?\s*(\S+)$', plain[i_pol])
        if inline and POLICY_RE.match(inline.group(1)):
            data['policy_number'] = inline.group(1)
        for v in values:
            vu = _plain(v).replace(' ', '')
            if 'policy_number' not in data and POLICY_RE.match(vu):
                data['policy_number'] = vu
            elif 'policy_number' in data and 'certificate_number' not in data and i_cert >= 0 \
                    and CERT_RE.match(vu) and not DATE_RE.search(v):
                data['certificate_number'] = vu
                break
    if 'certificate_number' not in data:
        match = re.search(r'CERTIFICADO\s+(?:NO\.?|NUM\.?|NUMERO|FOLIO)?\s*([A-Z]{0,4}-?\d{1,12})\b', joined)
        if match:
            data['certificate_number'] = match.group(1)
    if 'policy_number' not in data:
        match = re.search(r'POLIZA\s+(?:NO\.?|NUM\.?|NUMERO)?\s*([A-Z]{1,5}-?\d{5,}[A-Z0-9-]*)', joined)
        if match:
            data['policy_number'] = match.group(1)

    if data.get('policy_number'):
        clean = POLICY_CLEAN_RE.match(data['policy_number'].replace(' ', ''))
        if clean:
            data['policy_number'] = clean.group(1)
    if data.get('certificate_number'):
        data['certificate_number'] = re.sub(r'(?<=\d)[A-Z]+$', '', data['certificate_number'])

    # Fechas
    for key, pattern in (('date_start', r'FECHA DE ALTA DEL CERTIFICADO'),
                         ('date_end', r'FECHA DE FIN DEL CERTIFICADO')):
        match = re.search(pattern + r'\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})', joined)
        if match:
            data[key] = _to_date(match.group(1))
    match = re.search(r'VIGENCIA DE LA POLIZA\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})\D{1,6}(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})',
                      joined)
    if match:
        data['policy_date_start'] = _to_date(match.group(1))
        data['policy_date_end'] = _to_date(match.group(2))
    # Respaldo: la etiqueta y su fecha vienen separadas (otra columna). Las fechas aparecen
    # en el mismo orden que las etiquetas: alta, fin, inicio y fin de la vigencia de la póliza.
    if not data.get('date_start'):
        pos = joined.find('FECHA DE ALTA')
        if pos >= 0:
            tail = joined[pos:pos + 600]
            stop = tail.find('CONTRATANTE')
            dates = [d for d in (_to_date(m.group(0)) for m in DATE_RE.finditer(tail[:stop] if stop > 0 else tail)) if d]
            keys = ('date_start', 'date_end', 'policy_date_start', 'policy_date_end')
            for key, value in zip(keys, dates):
                data[key] = value      # etiquetas sin fecha al lado: manda el orden

    # Secciones Contratante y Asegurado
    i_con = _find(plain, 'CONTRATANTE')
    i_ase = _find(plain, 'ASEGURADO', max(i_con, 0))
    if i_con >= 0:
        end = i_ase if i_ase > i_con else min(len(tokens), i_con + 12)
        data.update(_section(tokens, plain, i_con + 1, end, CONTRACTOR_LABELS, 'contractor'))
    if i_ase >= 0:
        end = next((i for i in range(i_ase + 1, len(tokens)) if plain[i].startswith(INSURED_END)), len(tokens))
        end = min(end, i_ase + 16)
        data.update(_section(tokens, plain, i_ase + 1, end, INSURED_LABELS, 'insured'))

    # Coberturas
    data['coverages'] = _coverages(tokens, plain)
    return {k: v for k, v in data.items() if v}


def _section(tokens, plain, start, end, labels, prefix):
    out = {}
    name = rfc = birth = sex = False
    address = []
    zone = 'name'
    for i in range(start, end):
        tok, pl = tokens[i].strip(), plain[i]
        if pl in labels:
            if pl == 'DOMICILIO':
                zone = 'address'
            elif pl == 'FECHA DE NACIMIENTO':
                zone = 'birth' if zone != 'address' else zone
            continue
        compact = pl.replace(' ', '')
        if RFC_RE.match(compact):
            rfc = compact
            continue
        if pl in SEX_WORDS:
            sex = SEX_WORDS[pl]
            continue
        if DATE_RE.fullmatch(pl.replace(' ', '')):
            birth = _to_date(pl)
            continue
        if zone == 'name' and not name:
            name = ' '.join(tok.split())
        elif zone == 'address':
            address.append(' '.join(tok.split()))
    out['%s_name' % prefix] = name
    out['%s_rfc' % prefix] = rfc
    out['%s_address' % prefix] = re.sub(r'\s*,(\s*,)+', ',', ', '.join(a.strip(' ,') for a in address if a.strip(' ,')))
    if prefix == 'insured':
        out['insured_birthdate'] = birth
        out['insured_sex'] = sex
    return out


def _coverages(tokens, plain):
    """Renglones de la tabla de coberturas: texto seguido de 1 a 3 importes / «No aplica»."""
    start = next((i for i, t in enumerate(plain) if t in ('DETERMINARLA', 'PERIODO DE ESPERA', 'ESPERA')), -1)
    if start < 0:
        start = next((i for i, t in enumerate(plain) if t in ('COBERTURA', 'COBERTURAS')), -1)
    if start < 0:
        return []
    header_words = {'SUMA ASEGURADA O', 'SUMA ASEGURADA', 'REGLA PARA', 'DETERMINARLA', 'DEDUCIBLE',
                    'PERIODO DE', 'ESPERA', 'PERIODO DE ESPERA', 'COBERTURA', 'COBERTURAS'}
    rows, current = [], None
    for i in range(start + 1, len(tokens)):
        tok, pl = tokens[i].strip(), plain[i]
        if pl in header_words:
            continue
        is_value = bool(MONEY_RE.match(tok.replace(' ', '')) or MONEY_RE.match(tok)
                        or pl in ('NO APLICA', 'AMPARADA', 'INCLUIDA', 'N/A'))
        if is_value:
            if current is not None and len(current['values']) < 3:
                current['values'].append(' '.join(tok.split()))
            continue
        if current is not None and current['values']:
            rows.append(current)
            current = None
        if len(tok) > 90:      # párrafo de condiciones: termina la tabla
            if rows:
                break
            continue
        current = {'name': ' '.join(tok.split()), 'values': []}
    if current is not None and current['values']:
        rows.append(current)
    result = []
    for row in rows:
        values = row['values'] + [''] * (3 - len(row['values']))
        sum_txt, ded_txt, wait_txt = values[:3]
        result.append({
            'name': row['name'],
            'sum_insured': _money(sum_txt) if '$' in sum_txt else 0.0,
            'sum_insured_text': sum_txt,
            'deductible': _money(ded_txt) if '$' in ded_txt else 0.0,
            'deductible_text': ded_txt,
            'waiting_period': wait_txt,
        })
    return result
