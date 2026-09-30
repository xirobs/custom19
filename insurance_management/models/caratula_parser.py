# -*- coding: utf-8 -*-
"""Lector de carátulas de póliza en PDF (formato Seguros Monterrey New York Life).

Reconstruye las líneas de la página a partir de la posición (x, y) de cada
texto y extrae los datos de encabezado y la tabla de beneficios por columnas.
No depende de herramientas externas: usa el lector PDF que ya trae Odoo.
"""

import io
import logging
import re
import unicodedata
from datetime import date

_logger = logging.getLogger(__name__)

MONTHS = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SEPT": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

# Etiqueta normalizada -> clave
HEADER_LABELS = {
    "PLAN BASICO": "plan_basic",
    "CONTRATANTE": "contractor",
    "ASEGURADO": "insured",
    "RESIDENCIA": "residence",
    "POLIZA NO.": "policy_number",
    "POLIZA NO": "policy_number",
    "TIPO DE POLIZA": "policy_kind",
    "FECHA DE EMISION": "emission_date",
    "FORMA DE PAGO": "payment_form",
    "MONEDA": "currency",
    "EDAD": "age",
    "SEXO": "gender",
    "OPCION DE": "settlement_option",
    "OPCION DE LIQUIDACION": "settlement_option",
    "LIQUIDACION": "settlement_option",
    # "FECHA DE" se resuelve por posición: izquierda = nacimiento, derecha = vencimiento
}
TABLE_STOP = ("DESIGNACION", "DESI", "ARTICULO", "ADVERTENCIAS", "CIUDAD DE MEXICO")


class CaratulaParseError(Exception):
    pass


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().upper()


def parse_date(value):
    match = re.search(r"(\d{1,2})\s*/\s*([A-Za-zÁÉÍÓÚáéíóú]{3,4})\s*/\s*(\d{4})", value or "")
    if match:
        month = MONTHS.get(normalize(match.group(2)))
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    return None


def parse_amount(value):
    value = (value or "").strip()
    if not value or set(value) <= set("-– "):
        return None
    cleaned = re.sub(r"[^\d.,-]", "", value).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Extracción de texto con posición
# ---------------------------------------------------------------------------
def _page_chunks(page):
    chunks = []

    def visitor(text, cm, tm, font_dict, font_size):
        if not text:
            return
        lines = [line for line in text.split("\n") if line.strip()]
        if len(lines) != 1:
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        scale = abs(tm[0] * cm[0]) or 1.0
        size = (font_size or 9.0) * scale
        chunks.append({"x": x, "y": y, "text": lines[0], "size": size})

    try:
        page.extract_text(visitor_text=visitor)
    except TypeError as err:
        raise CaratulaParseError(
            "La librería PDF instalada es muy antigua para leer posiciones de texto "
            "(se requiere PyPDF2 2.x o pypdf)."
        ) from err
    return chunks


def _build_lines(chunks, y_tolerance=2.5):
    rows = []
    for chunk in sorted(chunks, key=lambda c: (-c["y"], c["x"])):
        if rows and abs(rows[-1]["y"] - chunk["y"]) <= y_tolerance:
            rows[-1]["chunks"].append(chunk)
        else:
            rows.append({"y": chunk["y"], "chunks": [chunk]})
    lines = []
    for row in rows:
        tokens = []
        for chunk in sorted(row["chunks"], key=lambda c: c["x"]):
            raw = chunk["text"]
            if tokens:
                last = tokens[-1]
                gap = chunk["x"] - last["end"]
                if gap < 2.0:
                    last["text"] += raw
                    last["end"] = chunk["x"] + len(raw) * chunk["size"] * 0.55
                    continue
            tokens.append({
                "x": chunk["x"],
                "text": raw,
                "end": chunk["x"] + len(raw) * chunk["size"] * 0.55,
            })
        for token in tokens:
            token["text"] = re.sub(r"\s+", " ", token["text"]).strip()
        tokens = [t for t in tokens if t["text"]]
        if tokens:
            lines.append({"y": row["y"], "tokens": tokens})
    return lines


# ---------------------------------------------------------------------------
# Interpretación
# ---------------------------------------------------------------------------
def _split_label(token_text):
    """Si el token es 'ETIQUETA valor' (fusionado), lo separa."""
    norm = normalize(token_text)
    for label in sorted(HEADER_LABELS, key=len, reverse=True):
        if norm == label:
            return label, None
        if norm.startswith(label + " "):
            return label, token_text.strip()[len(label):].strip() or None
    return None, None


def _parse_header(lines, page_mid):
    data = {}
    for line in lines:
        tokens = line["tokens"]
        for index, token in enumerate(tokens):
            norm = normalize(token["text"])
            next_value = tokens[index + 1]["text"] if index + 1 < len(tokens) else None
            if norm == "FECHA DE" or norm.startswith("FECHA DE ") and parse_date(token["text"]):
                value = next_value if norm == "FECHA DE" else token["text"]
                if value and parse_date(value):
                    key = "maturity_date" if token["x"] > page_mid else "birth_date"
                    data.setdefault(key, parse_date(value))
                continue
            label, inline_value = _split_label(token["text"])
            if not label:
                continue
            key = HEADER_LABELS[label]
            value = inline_value
            if value is None and next_value is not None and not _split_label(next_value)[0]:
                value = next_value
            if value and key not in data:
                data[key] = value
    if data.get("emission_date"):
        data["emission_date"] = parse_date(data["emission_date"])
    if data.get("age"):
        match = re.search(r"\d+", data["age"])
        data["age"] = int(match.group(0)) if match else False
    return data


def _parse_address(lines):
    start = None
    for index, line in enumerate(lines):
        if normalize(line["tokens"][0]["text"]) == "DOMICILIO":
            start = index
            break
    if start is None:
        return {}
    parts = []
    for line in lines[start:]:
        first = normalize(line["tokens"][0]["text"])
        if line is not lines[start] and (
            first.startswith("OPCION") or first.startswith("BENEFICIOS")
            or any(normalize(t["text"]) in ("PERIODO", "SUMA ASEGURADA", "SUMA") for t in line["tokens"])
        ):
            break
        texts = [t["text"] for t in line["tokens"] if t["x"] < 380]
        if texts and normalize(texts[0]) == "DOMICILIO":
            texts = texts[1:]
        if texts:
            parts.append(" ".join(texts))
    full = " ".join(parts)
    result = {"address": full}
    zip_match = re.search(r"C\.?\s*P\.?\s*(\d{5})", full)
    if zip_match:
        result["zip"] = zip_match.group(1)
        result["street"] = full[:zip_match.start()].strip(" ,")
        tail = full[zip_match.end():].strip(" ,")
        if "," in tail:
            city, state = [p.strip() for p in tail.split(",", 1)]
            result["city"], result["state"] = city, state
        elif tail:
            result["city"] = tail
    else:
        result["street"] = full
    return result


def _parse_benefits(lines):
    header_index = None
    for index, line in enumerate(lines):
        if any(normalize(t["text"]) == "BENEFICIOS" for t in line["tokens"]):
            header_index = index
            break
    if header_index is None:
        return [], {}
    header_y = lines[header_index]["y"]
    header_tokens = []
    for line in lines:
        if abs(line["y"] - header_y) <= 26:
            header_tokens += [(t["x"], normalize(t["text"])) for t in line["tokens"]]

    def anchor(predicate):
        xs = [x for x, text in header_tokens if predicate(text)]
        return min(xs) if xs else None

    anchors = {
        "benefit": anchor(lambda t: t == "BENEFICIOS"),
        "insured_amount": anchor(lambda t: t.startswith("SUMA") or t in ("ASEGURADA", "INICIAL")),
        "annex": anchor(lambda t: t == "ANEXO"),
        "effective_date": anchor(lambda t: t in ("FECHA DE", "EFECTIVIDAD")),
        "coverage_years": anchor(lambda t: t.startswith("COBER")),
        "premium": anchor(lambda t: t.startswith("PRIMA")),
    }
    cober_x = anchors["coverage_years"] or 0
    pago = [x for x, t in header_tokens if t in ("DE PAGO", "PAGO", "DE") and x > cober_x]
    anchors["payment_years"] = min(pago) if pago else None
    anchors = {k: v for k, v in anchors.items() if v is not None}
    if "benefit" not in anchors or "premium" not in anchors:
        return [], {}
    ordered = sorted(anchors.items(), key=lambda item: item[1])
    bounds = []
    for index, (key, x) in enumerate(ordered):
        right = (x + ordered[index + 1][1]) / 2.0 if index + 1 < len(ordered) else float("inf")
        bounds.append((key, right))

    def column_of(x):
        for key, right in bounds:
            if x < right:
                return key
        return bounds[-1][0]

    body_top = min(line["y"] for line in lines if abs(line["y"] - header_y) <= 26) - 2
    rows, section, totals = [], "", {}
    for line in lines:
        if line["y"] >= body_top:
            continue
        joined = normalize(" ".join(t["text"] for t in line["tokens"]))
        if "PRIMA PLANEADA" in joined:
            totals["planned_premium"] = parse_amount(line["tokens"][-1]["text"])
            continue
        if "TOTAL ANUAL" in joined or "PRIMA BASICA TOTAL" in joined:
            amount = parse_amount(line["tokens"][-1]["text"])
            key = "total_annual" if "TOTAL ANUAL" in joined else "basic_total"
            totals[key] = amount
            continue
        if any(joined.startswith(stop) for stop in TABLE_STOP):
            if rows:
                break
            continue
        cells = {}
        for token in line["tokens"]:
            col = column_of(token["x"])
            cells[col] = (cells.get(col, "") + " " + token["text"]).strip()
        if set(cells) == {"benefit"}:
            section = cells["benefit"]
            continue
        if "benefit" in cells:
            rows.append({
                "section": section,
                "code": cells["benefit"],
                "insured_amount_raw": cells.get("insured_amount", ""),
                "annex": [cells["annex"]] if cells.get("annex") else [],
                "effective_date_raw": cells.get("effective_date", ""),
                "coverage_years": cells.get("coverage_years", ""),
                "payment_years": cells.get("payment_years", ""),
                "premium_raw": cells.get("premium", ""),
            })
        elif rows and cells.get("annex"):
            rows[-1]["annex"].append(cells["annex"])
        elif not rows and not cells:
            continue
    benefits = []
    for row in rows:
        premium_norm = normalize(row["premium_raw"])
        benefits.append({
            "section": row["section"],
            "code": row["code"],
            "insured_amount": parse_amount(row["insured_amount_raw"]),
            "annex": ", ".join(row["annex"]),
            "effective_date": parse_date(row["effective_date_raw"]),
            "coverage_years": row["coverage_years"],
            "payment_years": row["payment_years"],
            "premium": 0.0 if "SIN COSTO" in premium_norm else (parse_amount(row["premium_raw"]) or 0.0),
            "no_cost": "SIN COSTO" in premium_norm,
        })
    return benefits, totals


def parse_caratula(pdf_bytes):
    """Devuelve un dict con los datos de la carátula."""
    try:
        from odoo.tools.pdf import PdfFileReader
    except ImportError:  # uso fuera de Odoo
        from PyPDF2 import PdfFileReader
    try:
        reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
        pages = list(reader.pages)
    except Exception as err:
        raise CaratulaParseError("El archivo no es un PDF válido: %s" % err) from err
    for page in pages:
        chunks = _page_chunks(page)
        if not any("PLAN B" in normalize(c["text"]) or "CONTRATANTE" in normalize(c["text"]) for c in chunks):
            continue
        lines = _build_lines(chunks)
        width = float(page.mediabox.width) if hasattr(page, "mediabox") else 612.0
        title = " ".join(
            t["text"] for line in lines for t in line["tokens"] if "CARATULA" in normalize(t["text"])
        )
        data = _parse_header(lines, width / 2.0 + 60)
        data.update(_parse_address(lines))
        benefits, totals = _parse_benefits(lines)
        data["benefits"] = benefits
        data.update(totals)
        data["title"] = title
        data["premium_total"] = (
            totals.get("total_annual")
            or totals.get("basic_total")
            or round(sum(b["premium"] for b in benefits), 2)
        )
        if not data.get("policy_number"):
            raise CaratulaParseError("No se encontró el número de póliza en la carátula.")
        return data
    raise CaratulaParseError(
        "No se encontró una carátula reconocible en el PDF. "
        "Si es un documento escaneado (imagen), no contiene texto que se pueda leer."
    )
