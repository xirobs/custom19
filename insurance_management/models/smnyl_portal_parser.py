# -*- coding: utf-8 -*-
"""Lectura de lo que se descarga a mano del portal de agentes de Seguros Monterrey (SMNYL).

* Lista de pólizas («Consultas › Pólizas», botón de exportar): un .xls que en realidad es HTML con la
  tabla de pólizas (número, contratante, asegurado principal, producto, estatus).
* Página de detalle «Consulta de datos generales de la Póliza»: guardada desde el navegador
  (Archivo › Guardar como) en .html. Se lee por etiquetas («Tipo de seguro», «Plan», …).

No se conecta al portal: solo lee archivos que el agente descargó con su propia sesión.
"""

import re
import unicodedata
from datetime import date

from lxml import html as lxml_html

POLICY_RE = re.compile(r"\b([A-Z]{2}\d{7,12})\b")
DATE_RE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})")

DETAIL_LABELS = {
    "tipo de seguro": "kind",
    "plan": "plan",
    "estatus": "status",
    "suma asegurada": "insured_amount",
    "moneda": "currency",
    "fecha emision": "emission_date",
    "forma de pago": "payment_form",
    "medio de cobro": "collection_channel",
    "banco": "bank",
    "numero de token/cuenta clabe": "account",
    "dia de cobro": "collection_day",
    "agente": "agent",
    "e-mail": "agent_email",
    "telefono": "agent_phone",
    "contratante": "contractor",
    "asegurados": "insured",
    "fecha de nacimiento": "birth_dates",
    "calle y numero": "street",
    "colonia": "colonia",
    "ciudad o municipio": "city",
    "estado": "state",
    "codigo postal": "zip",
    "pais": "country",
    "correo electronico": "email",
    "telefono particular": "phone",
    "telefono oficina": "phone_office",
}


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace(":", " ").split())


def decode(data):
    """Los archivos del portal vienen en ISO-8859-1 (a veces UTF-8 si el navegador los guardó)."""
    if isinstance(data, str):
        return data
    head = data[:3000].lower()
    if b"utf-8" in head:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_date(text):
    match = DATE_RE.search(text or "")
    if not match:
        return False
    day, month, year = (int(x) for x in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return False


def parse_amount(text):
    clean = re.sub(r"[^\d.\-]", "", (text or "").replace(",", ""))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0


def _lines(node):
    return [" ".join(t.split()) for t in node.itertext() if t and t.strip()]


def detect(data):
    """'list', 'detail' o None."""
    text = decode(data)
    low = text.lower()
    if "smnyl-bundle" in low:
        return "bundle"
    if "gvpollist" in low:
        return "list"
    if "consulta de datos generales" in norm(text) or ("tipo de seguro" in norm(text) and "asegurados" in norm(text)):
        return "detail"
    return None


def parse_list(data):
    """Filas de la lista de pólizas: number, contractor, insured, product, status."""
    doc = lxml_html.fromstring(decode(data))
    rows = []
    for table in doc.xpath("//table[contains(translate(@id,'GVPOLIST','gvpolist'),'gvpollist')]"):
        for tr in table.xpath(".//tr"):
            cells = [" ".join(td.text_content().split()) for td in tr.xpath("./td")]
            if len(cells) < 5 or not POLICY_RE.fullmatch(cells[0] or ""):
                continue
            rows.append({
                "number": cells[0], "contractor": cells[1], "insured": cells[2],
                "product": cells[3], "status": cells[4],
            })
    return rows


def split_bundle(data):
    """Archivo único generado por el script de descarga: lista + todas las páginas de detalle.

    Devuelve (filas_de_lista, [(número, html_de_detalle_en_bytes), ...]).
    """
    text = decode(data)
    doc = lxml_html.fromstring(text)
    rows = parse_list(text)
    details = []
    for sec in doc.xpath("//section[contains(concat(' ', normalize-space(@class), ' '), ' smnyl-detalle ')]"):
        number = (sec.get("data-poliza") or "").strip()
        chunk = lxml_html.tostring(sec, encoding="unicode")
        page = "<html><head><meta charset='utf-8'/></head><body>%s</body></html>" % chunk
        details.append((number, page.encode("utf-8")))
    return rows, details


def parse_detail(data):
    """Datos de la página «Consulta de datos generales de la Póliza»."""
    text = decode(data)
    doc = lxml_html.fromstring(text)
    out = {}
    head = POLICY_RE.search(text)
    title = re.search(r"Consulta de datos generales de la P\S*liza\s*:?\s*([A-Z]{2}\d{7,12})", text)
    hint = re.search(r'data-poliza="([A-Z]{2}\d{7,12})"', text)
    out["number"] = (hint.group(1) if hint else
                     title.group(1) if title else (head.group(1) if head else False))

    cells = doc.xpath("//td|//th")
    labels = {norm(" ".join(_lines(c))): i for i, c in reversed(list(enumerate(cells)))}
    label_idx = set()
    for key in DETAIL_LABELS:
        if key in labels:
            label_idx.add(labels[key])
    for key, field in DETAIL_LABELS.items():
        idx = labels.get(key)
        if idx is None:
            continue
        value_lines = []
        # el valor es la siguiente celda con texto que no sea otra etiqueta
        for j in range(idx + 1, min(idx + 3, len(cells))):
            if j in label_idx:
                break
            value_lines = _lines(cells[j])
            if value_lines:
                break
        out[field] = value_lines

    def one(field):
        return " ".join(out.get(field) or []).strip()

    insured, birth = out.get("insured") or [], out.get("birth_dates") or []
    people = []
    for i, name in enumerate(insured):
        clean = name.lstrip("*").strip()
        if not clean:
            continue
        people.append({
            "name": clean,
            "principal": name.strip().startswith("*"),
            "birth_date": parse_date(birth[i]) if i < len(birth) else False,
        })
    if people and not any(p["principal"] for p in people):
        people[0]["principal"] = True

    contractor = one("contractor")
    if POLICY_RE.fullmatch(contractor or ""):
        contractor = ""  # el portal a veces muestra el número de póliza en lugar del contratante
    agent = one("agent")
    agent_code = re.search(r"(\d{3,})\s*$", agent)
    return {
        "number": out["number"],
        "kind": one("kind"),
        "plan": one("plan"),
        "status": one("status"),
        "insured_amount": parse_amount(one("insured_amount")),
        "currency": one("currency"),
        "emission_date": parse_date(one("emission_date")),
        "payment_form": one("payment_form"),
        "collection_channel": one("collection_channel"),
        "bank": one("bank"),
        "collection_day": int(parse_amount(one("collection_day")) or 0),
        "agent_name": re.sub(r"\s*\d{3,}\s*$", "", agent).strip(),
        "agent_code": agent_code.group(1) if agent_code else "",
        "contractor": contractor,
        "people": people,
        "street": one("street"),
        "colonia": one("colonia"),
        "city": one("city"),
        "state": one("state"),
        "zip": one("zip"),
        "country": one("country"),
        "email": one("email"),
        "phone": one("phone") or one("phone_office"),
    }
