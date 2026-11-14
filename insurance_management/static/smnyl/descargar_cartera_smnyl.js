/*
 * Vive Seguro · Descarga de cartera del portal de agentes Seguros Monterrey (SMNYL)
 *
 * Qué hace: desde la pantalla «Consultas › Pólizas» (lista de pólizas), con TU sesión abierta,
 * recorre todas las páginas de la lista, abre el detalle de cada póliza en segundo plano
 * y al terminar descarga UN solo archivo (smnyl_cartera_AAAA-MM-DD.html) para importarlo en
 * Vive Seguro › Pólizas › Importar del portal Seguros Monterrey.
 *
 * No guarda ni envía tu contraseña; no manda datos a ningún otro sitio: todo se queda en tu navegador
 * hasta que se descarga el archivo.
 *
 * Uso: en la lista de pólizas abre la consola (Chrome: Ver › Opciones para desarrolladores ›
 * Consola de JavaScript; Safari: Desarrollo › Mostrar consola), pega este código y pulsa Enter.
 * Para probar primero con pocas pólizas cambia LIMITE a 3.
 */
(async () => {
  const LIMITE = 0;        // 0 = todas; pon 3 para una prueba rápida
  const PAUSA_MS = 1200;   // pausa entre pólizas para no saturar el portal
  const SOLO_VIGENTES = false; // true = omite las anuladas/canceladas

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const parser = new DOMParser();
  const POLIZA = /^[A-Z]{2}\d{7,12}$/;
  const charset = (document.characterSet || "utf-8").toLowerCase();

  // ---------- panel de avance ----------
  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;z-index:99999;right:16px;bottom:16px;background:#4b2a7b;color:#fff;" +
    "font:14px Arial;padding:12px 16px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:340px";
  document.body.appendChild(panel);
  let cancelado = false;
  const say = (html) => {
    panel.innerHTML = "<b>Vive Seguro · descarga de cartera</b><br>" + html +
      '<br><a href="#" id="vs_stop" style="color:#ffd27a">Detener y descargar lo que lleva</a>';
    const stop = document.getElementById("vs_stop");
    if (stop) stop.onclick = (e) => { e.preventDefault(); cancelado = true; };
  };

  const decode = async (resp) => {
    const buf = await resp.arrayBuffer();
    const ct = resp.headers.get("content-type") || "";
    const m = ct.match(/charset=([\w-]+)/i);
    try { return new TextDecoder((m && m[1]) || charset).decode(buf); }
    catch (e) { return new TextDecoder("utf-8").decode(buf); }
  };

  const gridOf = (doc) => doc.querySelector("table[id$='GVPolList'], table[id*='GVPolList']");

  async function postback(fromDoc, baseUrl, target, arg) {
    const form = fromDoc.querySelector("form");
    const body = new URLSearchParams();
    form.querySelectorAll("input, select, textarea").forEach((el) => {
      if (!el.name) return;
      const t = (el.type || "").toLowerCase();
      if (["submit", "button", "image", "file", "reset"].includes(t)) return;
      if ((t === "checkbox" || t === "radio") && !el.checked) return;
      body.append(el.name, el.value);
    });
    body.set("__EVENTTARGET", target);
    body.set("__EVENTARGUMENT", arg || "");
    const action = new URL(form.getAttribute("action") || baseUrl, baseUrl).href;
    const resp = await fetch(action, {
      method: "POST", body, credentials: "include",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    return { html: await decode(resp), url: resp.url || action };
  }

  async function get(url) {
    const resp = await fetch(url, { credentials: "include" });
    return { html: await decode(resp), url: resp.url || url };
  }

  const esDetalle = (html) => /consulta de datos generales/i.test(html) ||
    (/tipo de seguro/i.test(html) && /asegurados/i.test(html));

  // Si el portal abre el detalle con una redirección por script / ventana nueva, la seguimos.
  function urlDeScript(html, base) {
    const m = html.match(/window\.open\(\s*['"]([^'"]+)['"]/i) ||
      html.match(/(?:window\.|document\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]/i) ||
      html.match(/<meta[^>]+http-equiv=["']?refresh[^>]+url=([^"'>]+)/i);
    return m ? new URL(m[1].replace(/&amp;/g, "&"), base).href : null;
  }

  function limpiar(html) {
    const d = parser.parseFromString(html, "text/html");
    d.querySelectorAll("script, style, link, iframe, input[type=hidden]").forEach((n) => n.remove());
    return d.body ? d.body.innerHTML : html;
  }

  const filas = (doc) => {
    const grid = gridOf(doc);
    if (!grid) return [];
    const out = [];
    grid.querySelectorAll("tr").forEach((tr) => {
      const tds = tr.querySelectorAll(":scope > td");
      if (tds.length < 5) return;
      const a = tds[0].querySelector("a[href*='__doPostBack']");
      const numero = tds[0].textContent.trim().split(/\s+/)[0];
      if (!a || !POLIZA.test(numero)) return;
      const m = a.getAttribute("href").match(/__doPostBack\('([^']+)','([^']*)'\)/);
      out.push({
        numero, target: m && m[1], arg: m && m[2],
        celdas: [...tds].slice(0, 5).map((td) => td.textContent.replace(/\s+/g, " ").trim()),
      });
    });
    return out;
  };

  // pager de la lista: siguiente página = el Page$N mayor más cercano a la actual
  function siguientePagina(doc, actual) {
    const grid = gridOf(doc);
    if (!grid) return null;
    let mejor = null;
    grid.querySelectorAll("a[href*='Page$']").forEach((a) => {
      const m = a.getAttribute("href").match(/__doPostBack\('([^']+)','Page\$(\d+)'\)/);
      if (!m) return;
      const n = parseInt(m[2], 10);
      if (n > actual && (!mejor || n < mejor.n)) mejor = { n, target: m[1] };
    });
    return mejor;
  }

  if (!gridOf(document)) {
    alert("Abre primero Consultas › Pólizas (la lista) y vuelve a pegar el código.");
    panel.remove();
    return;
  }

  const lista = [], detalles = [], errores = [];
  const vistos = new Set();
  let listDoc = document, listUrl = location.href, pagina = 1, total = 0;

  try {
    while (!cancelado) {
      const rows = filas(listDoc);
      for (const r of rows) {
        if (cancelado || (LIMITE && total >= LIMITE)) break;
        if (vistos.has(r.numero)) continue;
        vistos.add(r.numero);
        lista.push(r.celdas);
        if (SOLO_VIGENTES && /anulad|cancelad/i.test(r.celdas[4] || "")) continue;
        total++;
        say(`Página ${pagina} · póliza ${total}: ${r.numero}<br>Detalles listos: ${detalles.length} · errores: ${errores.length}`);
        try {
          let res = await postback(listDoc, listUrl, r.target, r.arg);
          if (!esDetalle(res.html)) {
            const next = urlDeScript(res.html, res.url);
            if (next) res = await get(next);
          }
          if (esDetalle(res.html)) {
            detalles.push(`<section class="smnyl-detalle" data-poliza="${r.numero}">${limpiar(res.html)}</section>`);
          } else {
            errores.push(r.numero);
            if (errores.length === 1) console.warn("Vive Seguro: respuesta inesperada para", r.numero, res.url, res.html.slice(0, 2000));
          }
        } catch (e) {
          errores.push(r.numero);
          console.warn("Vive Seguro: error en", r.numero, e);
        }
        await sleep(PAUSA_MS);
      }
      if (cancelado || (LIMITE && total >= LIMITE)) break;
      const sig = siguientePagina(listDoc, pagina);
      if (!sig) break;
      say(`Cambiando a la página ${sig.n}…`);
      const res = await postback(listDoc, listUrl, sig.target, "Page$" + sig.n);
      listDoc = parser.parseFromString(res.html, "text/html");
      listUrl = res.url;
      pagina = sig.n;
      if (!filas(listDoc).length) break;
      await sleep(PAUSA_MS);
    }
  } finally {
    const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
    const tabla = '<table id="GVPolList_bundle">' + lista.map((c) =>
      "<tr>" + c.map((v) => `<td>${esc(v)}</td>`).join("") + "</tr>").join("") + "</table>";
    const _d = new Date(); const hoy = `${_d.getFullYear()}-${String(_d.getMonth() + 1).padStart(2, "0")}-${String(_d.getDate()).padStart(2, "0")}`;
    const archivo = `<!doctype html><html><head><meta charset="utf-8"><title>Cartera SMNYL ${hoy}</title></head>` +
      `<body><div class="smnyl-bundle" data-fecha="${hoy}">${tabla}${detalles.join("\n")}</div></body></html>`;
    const blob = new Blob([archivo], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `smnyl_cartera_${hoy}.html`;
    document.body.appendChild(a);
    a.click();
    say(`Listo: ${lista.length} pólizas en la lista, ${detalles.length} con detalle` +
      (errores.length ? `, <b>${errores.length} sin detalle</b> (ver consola)` : "") +
      `.<br>Se descargó <b>${a.download}</b>. Súbelo en Vive Seguro.`);
    if (errores.length) console.log("Vive Seguro · pólizas sin detalle:", errores.join(", "));
  }
})();
