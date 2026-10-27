# -*- coding: utf-8 -*-
"""Lectura de PDF con posición del texto (sin IA): tablas de programa y calendarios en cuadrícula.

Funciona con pypdf o PyPDF2 (lo que tenga el servidor). Si la versión instalada no da posiciones, regresa
una lista vacía y quien llama usa el texto plano.
"""
import calendar
import datetime
import io
import logging
import re
import unicodedata

_logger = logging.getLogger(__name__)

MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
          'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
WEEKDAYS = {'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sabado': 5, 'domingo': 6}


def _plain(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()


def _clean(text):
    text = (text or '').replace('\t', ' ').replace('​', '').replace('\xad', '')
    return re.sub(r'\s+', ' ', text).strip()


def pdf_items(content, max_pages=10):
    """[{'page', 'x', 'y', 'text', 'size'}] con la posición de cada trozo de texto (y crece hacia arriba)."""
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
    except Exception:  # noqa: BLE001
        _logger.info('No se pudo abrir el PDF', exc_info=True)
        return []
    items = []
    for page_no, page in enumerate(reader.pages[:max_pages]):
        found = []

        def visitor(text, cm, tm, font_dict, font_size, found=found):
            text = _clean(text)
            if not text:
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            scale = (cm[0] ** 2 + cm[1] ** 2) ** 0.5 or 1.0
            found.append({'page': page_no, 'x': x, 'y': y, 'text': text, 'size': (font_size or 0) * scale})
        try:
            page.extract_text(visitor_text=visitor)
        except TypeError:  # PyPDF2 antiguo: sin posiciones
            return []
        except Exception:  # noqa: BLE001
            _logger.info('No se pudo leer la página %s del PDF', page_no, exc_info=True)
            continue
        items.extend(found)
    return items


def _width(item):
    return len(item['text']) * (item['size'] or 6) * 0.5


def _center(item):
    return item['x'] + _width(item) / 2


# ---------------------------------------------------------------- tablas (programa de la junta)
def table_rows(items, header_match):
    """Reconstruye una tabla: [[texto por columna], ...] con el encabezado como primera fila.

    `header_match(row)` recibe una lista de textos y dice si parece el encabezado (p. ej. tiene HORA y ACTIVIDAD).
    Las filas se anclan en la columna de la hora (o del nombre) y cada trozo de texto se asigna a la fila más
    cercana en vertical, porque las celdas con varios renglones se centran.
    """
    for page in sorted({i['page'] for i in items}):
        page_items = [i for i in items if i['page'] == page]
        # renglones por coordenada y
        lines = []
        for item in sorted(page_items, key=lambda i: (-i['y'], i['x'])):
            if lines and abs(lines[-1][0] - item['y']) <= 2.5:
                lines[-1][1].append(item)
            else:
                lines.append([item['y'], [item]])
        header_line = next((ln for ln in lines if header_match([i['text'] for i in sorted(ln[1], key=lambda i: i['x'])])),
                           None)
        if not header_line:
            continue
        head_y = header_line[0]
        size = max(i['size'] for i in header_line[1]) or 6
        # encabezado: lo que esté hasta ~4 renglones arriba o abajo del renglón principal (encabezados partidos)
        band = [i for i in page_items if abs(i['y'] - head_y) <= size * 4.2 and i['y'] >= head_y - size * 2.6]
        band.sort(key=_center)
        columns = []
        for item in band:
            if columns and _center(item) - columns[-1]['right'] < size * 1.2:
                columns[-1]['items'].append(item)
                columns[-1]['right'] = max(columns[-1]['right'], item['x'] + _width(item))
            else:
                columns.append({'items': [item], 'right': item['x'] + _width(item)})
        for col in columns:
            col['center'] = sum(_center(i) for i in col['items']) / len(col['items'])
            words = [i['text'] for i in sorted(col['items'], key=lambda i: (-i['y'], i['x']))]
            label = ''
            for w in words:
                label = label[:-1] + w if label.endswith(('-', '‐')) else (label + ' ' + w).strip()
            col['label'] = label.replace('‐', '')
        if len(columns) < 2:
            continue
        bottom = min(i['y'] for i in band)
        body = [i for i in page_items if i['y'] < bottom - 1]

        def col_of(item):
            c = _center(item)
            return min(range(len(columns)), key=lambda k: abs(columns[k]['center'] - c))
        labels = [_plain(c['label']) for c in columns]
        anchor_col = next((k for k, lab in enumerate(labels) if lab.startswith('hora') or lab == 'horario'), None)
        if anchor_col is None:
            anchor_col = next((k for k, lab in enumerate(labels) if 'actividad' in lab), 0)
        anchors = []
        for item in sorted((i for i in body if col_of(i) == anchor_col), key=lambda i: -i['y']):
            if anchor_col is not None and labels[anchor_col].startswith('hora') \
                    and not re.match(r'^\d{1,2}[:.]\d{2}', item['text']):
                continue  # "a 16:15" es la hora de término de la misma fila
            if anchors and anchors[-1] - item['y'] < size * 1.6:
                continue
            anchors.append(item['y'])
        if not anchors:
            continue
        rows = [[''] * len(columns) for _a in anchors]
        steps = sorted(a - b for a, b in zip(anchors, anchors[1:]))
        step = steps[len(steps) // 2] if steps else size * 4
        for item in sorted(body, key=lambda i: (-i['y'], i['x'])):
            if item['y'] < anchors[-1] - max(step * 0.7, size * 1.8) or item['y'] > anchors[0] + max(step * 0.7, size * 1.8):
                continue  # pie de página o datos arriba de la tabla
            r = min(range(len(anchors)), key=lambda k: abs(anchors[k] - item['y']))
            c = col_of(item)
            rows[r][c] = (rows[r][c] + ' ' + item['text']).strip()
        return [[c['label'] for c in columns]] + rows
    return []


# ---------------------------------------------------------------- calendarios en cuadrícula
def calendar_events(items):
    """Eventos de un calendario en cuadrícula (meses en columnas, número del día arriba a la izquierda de cada
    celda y el nombre de la actividad debajo). [{'date', 'title', 'notes'}] más los avisos del pie."""
    events, footnotes = [], []
    for page in sorted({i['page'] for i in items}):
        page_items = [i for i in items if i['page'] == page]
        months = []
        for item in page_items:
            m = re.match(r'^([a-z]+)\s+(\d{4})$', _plain(item['text']))
            if m and m.group(1) in MONTHS:
                months.append({'month': MONTHS[m.group(1)], 'year': int(m.group(2)), 'item': item})
        if not months:
            continue
        months.sort(key=lambda m: m['item']['x'])
        head_y = min(m['item']['y'] for m in months)
        day_items = [i for i in page_items if re.fullmatch(r'\d{1,2}', i['text']) and i['y'] < head_y
                     and i['size'] >= 10]
        if not day_items:
            continue
        number_size = max(i['size'] for i in day_items)
        # Bloque de cada mes: desde su primera columna de días hasta la del mes siguiente
        weekday_heads = sorted((i for i in page_items if _plain(i['text']) in WEEKDAYS and i['y'] < head_y),
                               key=lambda i: i['x'])
        starts = []
        if weekday_heads:
            first_name = _plain(weekday_heads[0]['text'])
            starts = [i['x'] for i in weekday_heads if _plain(i['text']) == first_name]
        if len(starts) != len(months):
            starts = [m['item']['x'] for m in months]
        cell_w = 9999
        xs = sorted({round(i['x']) for i in day_items})
        gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 20]
        if gaps:
            cell_w = min(gaps)
        bounds = []
        for k, start in enumerate(starts):
            left = start - cell_w * 0.5
            right = starts[k + 1] - cell_w * 0.5 if k + 1 < len(starts) else 99999
            bounds.append((left, right))

        def month_of(x):
            for k, (left, right) in enumerate(bounds):
                if left <= x < right:
                    return k
            return None
        days = []
        for item in day_items:
            k = month_of(item['x'])
            if k is None:
                continue
            info = months[k]
            last = calendar.monthrange(info['year'], info['month'])[1]
            day = int(item['text'])
            if 1 <= day <= last:
                days.append({'date': datetime.date(info['year'], info['month'], day), 'x': item['x'],
                             'y': item['y'], 'k': k})
        texts = [i for i in page_items if i['y'] < head_y and i['size'] < number_size * 0.6
                 and not re.fullmatch(r'\d{1,2}', i['text']) and _plain(i['text']) not in WEEKDAYS]
        footer_y = min(d['y'] for d in days) - number_size * 6 if days else 0
        cells = {}
        for item in texts:
            k = month_of(item['x'])
            candidates = [d for d in days if d['k'] == k and d['x'] - 6 <= item['x'] < d['x'] + cell_w - 4
                          and d['y'] > item['y']]
            if not candidates:
                continue
            cell = min(candidates, key=lambda d: d['y'] - item['y'])
            if cell['y'] - item['y'] > number_size * 9:
                continue
            cells.setdefault(cell['date'], []).append(item)
        for day, parts in sorted(cells.items()):
            parts.sort(key=lambda i: -i['y'])
            title_size = max(p['size'] for p in parts)
            current = None
            for part in parts:
                if part['size'] >= title_size - 0.3:
                    current = {'date': day, 'title': part['text'], 'notes': []}
                    events.append(current)
                elif current:
                    current['notes'].append(part['text'])
                else:
                    current = {'date': day, 'title': part['text'], 'notes': []}
                    events.append(current)
        footnotes += [i['text'] for i in sorted(page_items, key=lambda i: -i['y'])
                      if i['y'] < footer_y and i['size'] >= number_size * 0.55]
    for ev in events:
        ev['notes'] = ' · '.join(ev['notes'])
    return events, footnotes


def merge_consecutive(events):
    """Une los días seguidos con el mismo título (campamento viernes-domingo) en una sola actividad."""
    merged = []
    for ev in sorted(events, key=lambda e: (_plain(e['title']), e['date'])):
        last = merged[-1] if merged else None
        if last and _plain(last['title']) == _plain(ev['title']) \
                and (ev['date'] - last['date_end']).days == 1:
            last['date_end'] = ev['date']
            if ev['notes'] and ev['notes'] not in last['notes']:
                last['notes'] = ' · '.join(x for x in (last['notes'], ev['notes']) if x)
            continue
        merged.append({'date': ev['date'], 'date_end': ev['date'], 'title': ev['title'], 'notes': ev['notes']})
    return sorted(merged, key=lambda e: (e['date'], e['title']))
