# -*- coding: utf-8 -*-
"""Lectura de listas de asegurados desde Excel o PDF (sin dependencias de Odoo).

Devuelven una lista de diccionarios con los campos del asegurado y una lista de avisos
por fila. La validación final (obligatorios, duplicados) la hace rss.import.batch.
"""
import io
import re
import unicodedata
from datetime import date, datetime, timedelta

FIELDS = ('first_name', 'second_name', 'last_name_father', 'last_name_mother', 'birthdate',
          'sex', 'tutor_name', 'tutor_phone', 'tutor_email')

# Encabezados reconocidos (normalizados: minúsculas, sin acentos ni signos)
HEADERS = {
    'full_name': ['nombre completo', 'nombre del asegurado', 'asegurado', 'nombre completo del asegurado',
                  'nombre y apellidos', 'nombre completo del scout'],
    'first_name': ['nombre', 'nombres', 'nombre s', 'primer nombre', 'nombre del scout'],
    'second_name': ['segundo nombre'],
    'last_name_father': ['apellido paterno', 'paterno', 'primer apellido', 'ap paterno', 'a paterno'],
    'last_name_mother': ['apellido materno', 'materno', 'segundo apellido', 'ap materno', 'a materno'],
    'birthdate': ['fecha de nacimiento', 'fecha nacimiento', 'nacimiento', 'f nacimiento', 'fec nac',
                  'fecha nac', 'f nac'],
    'sex': ['sexo', 'genero'],
    'age': ['edad'],
    'tutor_name': ['padre o tutor', 'nombre del padre o tutor', 'tutor', 'nombre del tutor',
                   'padre madre o tutor', 'nombre completo del padre o tutor', 'padre tutor',
                   'nombre del padre madre o tutor', 'responsable'],
    'tutor_phone': ['telefono', 'telefono de emergencia', 'telefono emergencia', 'celular', 'whatsapp',
                    'tel', 'tel emergencia', 'telefono del tutor', 'numero de emergencia',
                    'numero telefono en caso de emergencia', 'telefono en caso de emergencia'],
    'tutor_email': ['correo', 'correo electronico', 'email', 'e mail', 'correo del tutor', 'mail'],
}

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
DATE_RE = re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b|\b(\d{4})-(\d{1,2})-(\d{1,2})\b')
PHONE_RE = re.compile(r'(?:\+?\d[\d\s\-()]{8,}\d)')

MALE = {'H', 'HOMBRE', 'MASCULINO', 'MASC', 'NINO', 'VARON', 'MALE', 'MAN'}
FEMALE = {'F', 'MUJER', 'FEMENINO', 'FEM', 'NINA', 'FEMALE', 'WOMAN'}


def norm(value):
    if value is None:
        return ''
    value = unicodedata.normalize('NFKD', str(value))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r'[^a-zA-Z0-9]+', ' ', value).lower()
    return ' '.join(value.split())


def clean(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return ' '.join(str(value).split())


def title(value):
    """'PÉREZ de la CRUZ' -> 'Pérez de la Cruz' (partículas en minúscula)."""
    value = clean(value)
    if not value:
        return ''
    small = {'de', 'del', 'la', 'las', 'los', 'y', 'san'}
    words = []
    for i, word in enumerate(value.lower().split()):
        words.append(word if (i and word in small) else word[:1].upper() + word[1:])
    return ' '.join(words)


def parse_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 1 < value < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))   # número de serie de Excel
    match = DATE_RE.search(str(value))
    if not match:
        return None
    try:
        if match.group(4):
            result = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return result if date(1900, 1, 1) <= result <= date.today() else None
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000 if year <= date.today().year % 100 else 1900
        result = date(year, month, day)
        return result if date(1900, 1, 1) <= result <= date.today() else None
    except ValueError:
        return None


def parse_sex(value, column_values=()):
    """'H'/'F'/'Masculino'... La 'M' sola es ambigua (Masculino o Mujer): se decide por la
    columna completa; si no se puede, se devuelve None para que lo revisen."""
    token = norm(value).upper()
    if not token:
        return None
    if token in MALE:
        return 'male'
    if token in FEMALE:
        return 'female'
    if token == 'M':
        tokens = {norm(v).upper() for v in column_values}
        if 'H' in tokens:
            return 'female'      # H/M -> M es Mujer
        if 'F' in tokens:
            return 'male'        # M/F -> M es Masculino
    return None


def split_first_names(value):
    parts = clean(value).split()
    if not parts:
        return '', ''
    return title(parts[0]), title(' '.join(parts[1:]))


PARTICLES = {'de', 'del', 'la', 'las', 'los', 'y', 'san', 'van', 'von', 'da', 'di'}


def _surname_units(tokens):
    """Agrupa partículas con la palabra siguiente: ['de','la','Cruz'] -> ['de la Cruz']."""
    units, pending = [], []
    for tok in tokens:
        if tok.lower() in PARTICLES:
            pending.append(tok)
            continue
        units.append(' '.join(pending + [tok]))
        pending = []
    if pending:
        units.append(' '.join(pending))
    return units


def split_full_name(value, surnames_first=False):
    """Divide un nombre completo suponiendo dos apellidos (respeta «de la Cruz»).
    El resultado se revisa en pantalla antes de guardar."""
    parts = clean(value).replace(',', ' ').split()
    if len(parts) < 2:
        return {'first_name': title(value)}
    if surnames_first:
        units = _surname_units(parts)
        if len(units) >= 3:
            father, mother = units[0], units[1]
            consumed = len(father.split()) + len(mother.split())
            names = parts[consumed:]
        else:
            father, mother, names = units[0], '', parts[len(units[0].split()):]
    else:
        units = _surname_units(parts)
        if len(units) >= 3:
            father, mother = units[-2], units[-1]
            consumed = len(father.split()) + len(mother.split())
            names = parts[:len(parts) - consumed]
        else:
            names, father, mother = parts[:1], ' '.join(parts[1:]), ''
    first, second = split_first_names(' '.join(names))
    return {'first_name': first, 'second_name': second, 'last_name_father': title(father),
            'last_name_mother': title(mother)}


def _match_header(cell):
    key = norm(cell)
    if not key:
        return None
    for field, names in HEADERS.items():
        if key in names:
            return field
    for field, names in HEADERS.items():
        if any(key.startswith(n) for n in names if len(n) > 5):
            return field
    return None


def _finish_row(raw, sex_column):
    """Convierte una fila cruda (campo -> valor) a valores del asegurado."""
    row = {}
    if raw.get('full_name') and not raw.get('last_name_father'):
        row.update(split_full_name(raw['full_name']))
    if raw.get('first_name'):
        if raw.get('second_name'):
            row['first_name'] = title(raw['first_name'])
            row['second_name'] = title(raw['second_name'])
        else:
            row['first_name'], row['second_name'] = split_first_names(raw['first_name'])
    for key in ('last_name_father', 'last_name_mother', 'tutor_name'):
        if raw.get(key):
            row[key] = title(raw[key])
    row['birthdate'] = parse_date(raw.get('birthdate'))
    row['sex'] = parse_sex(raw.get('sex'), sex_column)
    phone = clean(raw.get('tutor_phone'))
    row['tutor_phone'] = phone
    email = EMAIL_RE.search(clean(raw.get('tutor_email')) or '')
    row['tutor_email'] = email.group(0).lower() if email else clean(raw.get('tutor_email')).lower()
    row['raw_sex'] = clean(raw.get('sex'))
    row['raw_birthdate'] = clean(raw.get('birthdate')) if not isinstance(raw.get('birthdate'), (date, datetime)) else ''
    return row


# ----------------------------------------------------------------------------
# Excel
# ----------------------------------------------------------------------------
def parse_excel(data):
    """Lee la primera hoja con encabezados reconocibles. Devuelve (filas, error)."""
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001
        return [], 'No se pudo abrir el archivo. Usa un Excel .xlsx (puedes partir de la plantilla).'
    for sheet in book.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        for idx, row in enumerate(rows[:15]):
            mapping = {}
            for col, cell in enumerate(row):
                field = _match_header(cell)
                if field and field not in mapping.values():
                    mapping[col] = field
            fields_found = set(mapping.values())
            if len(fields_found) >= 3 and (('first_name' in fields_found or 'full_name' in fields_found)):
                body = [r for r in rows[idx + 1:] if r and any(clean(c) for c in r)]
                sex_col = [r[c] for r in body for c, f in mapping.items() if f == 'sex' and c < len(r)]
                result = []
                for r in body:
                    raw = {f: r[c] for c, f in mapping.items() if c < len(r)}
                    if not any(clean(v) for k, v in raw.items() if k in ('first_name', 'full_name',
                                                                           'last_name_father')):
                        continue
                    result.append(_finish_row(raw, sex_col))
                return result, None
    return [], ('No encontré los encabezados. La primera fila debe decir, por ejemplo: Nombre, '
                'Apellido paterno, Apellido materno, Fecha de nacimiento, Sexo, Padre o tutor, '
                'Teléfono de emergencia, Correo.')


# ----------------------------------------------------------------------------
# PDF (listas en tabla con texto seleccionable)
# ----------------------------------------------------------------------------
SEX_WORDS = MALE | FEMALE | {'M'}
NOISE = {'#', 'NO', 'NUM'}


def _pdf_text_lines(data):
    try:
        from odoo.tools.pdf import PdfFileReader
    except ImportError:  # fuera de Odoo (pruebas)
        from pypdf import PdfReader as PdfFileReader
    reader = PdfFileReader(io.BytesIO(data), strict=False)
    lines = []
    for page in reader.pages:
        try:
            # pypdf >= 3.17: respeta la separación de columnas
            text = page.extract_text(extraction_mode='layout') or ''
        except TypeError:
            text = page.extract_text() or ''
        for line in text.splitlines():
            if line.strip():
                # Separa letras y números pegados por la extracción ("Lópe222" -> "Lópe 222"),
                # sin tocar los correos electrónicos.
                emails = EMAIL_RE.findall(line)
                for i, email in enumerate(emails):
                    line = line.replace(email, ' \x00%d\x00 ' % i, 1)
                line = re.sub(r'(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])', ' ', line)
                for i, email in enumerate(emails):
                    line = line.replace('\x00 %d \x00' % i, email).replace('\x00%d\x00' % i, email)
                lines.append(' '.join(line.split()))
    return lines


def _parse_pdf_cells(lines):
    """PDF cuya extracción entrega una celda por renglón: se reconocen los encabezados
    como columnas y cada fila se arma alrededor de su fecha de nacimiento."""
    columns, start = [], None
    for idx, line in enumerate(lines):
        field = _match_header(line)
        is_number_col = norm(line) in ('', 'no', 'num', 'numero') or line.strip() == '#'
        if field or (is_number_col and not columns):
            if start is None:
                start = idx
            columns.append(field or 'number')
        elif columns:
            if len([c for c in columns if c != 'number']) >= 3:
                break
            columns, start = [], None
    if 'birthdate' not in columns or len(columns) < 4:
        return []
    cells = lines[start + len(columns):]
    k, width = columns.index('birthdate'), len(columns)
    raw_rows, sex_col = [], []
    for pos, cell in enumerate(cells):
        if not DATE_RE.fullmatch(cell.strip()) or pos - k < 0:
            continue
        chunk = cells[pos - k: pos - k + width]
        if len(chunk) < width:
            continue
        raw = {col: val for col, val in zip(columns, chunk) if col not in ('number', 'age')}
        raw_rows.append(raw)
        sex_col.append(raw.get('sex'))
    return [_finish_row(raw, sex_col) for raw in raw_rows
            if any(clean(raw.get(f)) for f in ('first_name', 'full_name', 'last_name_father'))]


def parse_pdf(data):
    """Lee listas en PDF: una persona por renglón, con su fecha de nacimiento.

    Funciona con PDF de texto (exportados de Excel o Word). Un escaneo o una foto no tienen
    texto que leer: en ese caso se pide el Excel.
    """
    try:
        lines = _pdf_text_lines(data)
    except Exception:  # noqa: BLE001
        return [], 'No se pudo leer el PDF.'
    if not lines:
        return [], ('El PDF no tiene texto que se pueda leer (parece escaneado o una foto). '
                    'Súbelo en Excel o como PDF exportado desde Excel o Word.')
    # ¿Los apellidos van antes del nombre? Se decide por el encabezado.
    surnames_first = True
    for line in lines[:30]:
        n = norm(line)
        if 'paterno' in n and 'nombre' in n:
            surnames_first = n.index('paterno') < n.index('nombre')
            break
    rows = []
    sex_tokens = []
    parsed = []
    for line in lines:
        match = DATE_RE.search(line)
        if not match:
            continue
        before, after = line[:match.start()], line[match.end():]
        name_tokens = [t for t in before.replace(',', ' ').split()
                       if not t.isdigit() and t.upper().strip('.') not in NOISE]
        if len(name_tokens) < 2:
            continue
        email = EMAIL_RE.search(after)
        rest = after.replace(email.group(0), ' ') if email else after
        phone = PHONE_RE.search(rest)
        rest = rest.replace(phone.group(0), ' ') if phone else rest
        tokens = rest.split()
        sex = ''
        if tokens and norm(tokens[0]).isdigit():          # edad
            tokens = tokens[1:]
        if tokens and norm(tokens[0]).upper() in SEX_WORDS:
            sex = tokens[0]
            tokens = tokens[1:]
        if tokens and norm(tokens[0]).isdigit():          # edad después del sexo
            tokens = tokens[1:]
        sex_tokens.append(sex)
        parsed.append((name_tokens, line[match.start():match.end()], sex, ' '.join(tokens),
                       phone.group(0) if phone else '', email.group(0) if email else ''))
    for name_tokens, birth, sex, tutor, phone, email in parsed:
        row = split_full_name(' '.join(name_tokens), surnames_first=surnames_first)
        row.update({
            'birthdate': parse_date(birth),
            'sex': parse_sex(sex, sex_tokens),
            'tutor_name': title(tutor),
            'tutor_phone': clean(phone),
            'tutor_email': email.lower(),
            'raw_sex': sex,
            'raw_birthdate': birth,
        })
        rows.append(row)
    cell_rows = _parse_pdf_cells(lines)
    if len(cell_rows) > len(rows):
        rows = cell_rows
    if not rows:
        return [], ('No encontré renglones con fecha de nacimiento en el PDF. Revisa que sea la lista '
                    'de asegurados o súbela en Excel.')
    return rows, None


# ----------------------------------------------------------------------------
# Plantilla
# ----------------------------------------------------------------------------
TEMPLATE_HEADERS = ['Nombre(s)', 'Apellido paterno', 'Apellido materno', 'Fecha de nacimiento',
                    'Sexo', 'Nombre completo del padre o tutor', 'Teléfono de emergencia',
                    'Correo electrónico']


def build_template_xlsx(group_name=''):
    import xlsxwriter
    output = io.BytesIO()
    book = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = book.add_worksheet('Asegurados')
    head = book.add_format({'bold': True, 'font_color': 'white', 'bg_color': '#6E0B14', 'border': 1,
                            'text_wrap': True, 'valign': 'vcenter', 'align': 'center'})
    note = book.add_format({'italic': True, 'font_color': '#5A6478'})
    date_f = book.add_format({'num_format': 'dd/mm/yyyy'})
    widths = [22, 18, 18, 16, 12, 32, 18, 30]
    for col, (label, width) in enumerate(zip(TEMPLATE_HEADERS, widths)):
        ws.write(0, col, label, head)
        ws.set_column(col, col, width, date_f if col == 3 else None)
    ws.set_row(0, 30)
    ws.write_row(1, 0, ['Ana Sofía', 'Pérez', 'López', '01/05/2014', 'Mujer', 'Laura López García',
                        '2221234567', 'laura@correo.com'])
    ws.data_validation(1, 4, 2000, 4, {'validate': 'list', 'source': ['Hombre', 'Mujer']})
    help_ws = book.add_worksheet('Instrucciones')
    tips = [
        'Una persona por renglón, a partir del renglón 2 (borra el ejemplo).',
        'Fecha de nacimiento en formato día/mes/año, p. ej. 01/05/2014.',
        'Sexo: Hombre o Mujer. La edad se calcula sola.',
        'Padre o tutor: nombre completo; teléfono para emergencias y correo electrónico.',
        'Si una persona ya está registrada (mismo nombre y fecha de nacimiento) se marca como repetida.',
    ]
    if group_name:
        tips.insert(0, 'Grupo: %s' % group_name)
    for i, tip in enumerate(tips):
        help_ws.write(i, 0, tip)
    help_ws.set_column(0, 0, 100)
    ws.freeze_panes(1, 0)
    book.close()
    return output.getvalue()
