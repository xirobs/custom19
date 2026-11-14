/*
 * Vive Seguro · Descarga de cartera del portal de agentes Seguros Monterrey (SMNYL) · v8
 *
 * Desde «Consultas › Pólizas» (la lista), con TU sesión abierta, entra a cada póliza
 * («DetallePoliza.aspx», el formulario «Consulta de datos generales») y copia su contenido.
 * Al final descarga UN archivo .csv (vive_seguro_cartera_smnyl_AAAA-MM-DD.csv): se abre en Excel y se importa en
 * Vive Seguro › Pólizas › Importar del portal Seguros Monterrey.
 *
 * Método rápido: selecciona la póliza en segundo plano y lee DetallePoliza.aspx.
 * Si el portal no lo permite, usa una ventana auxiliar que da clic en cada póliza y en «Regresar».
 * No guarda ni envía tu contraseña ni manda datos a otro sitio.
 *
 * Uso: en la lista de pólizas abre la consola (Cmd + Option + J), pega este código y Enter.
 * LIMITE = 3 para probar; 0 para todas.
 */
(async () => {
  const LIMITE = 3;            // 0 = todas
  // Para bajar solo ciertas pólizas, escríbelas aquí entre comillas, separadas por comas. Vacío = todas.
  const SOLO_ESTAS = [];
  const REINTENTOS = 2;        // vueltas extra para las que salgan «sin detalle»
  const SOLO_VIGENTES = false; // true = omite anuladas / canceladas
  const PAUSA_MS = 700;
  const ESPERA_MAX_MS = 45000;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const parser = new DOMParser();
  const POLIZA = /^[A-Z]{2}\d{7,12}$/;
  const LISTA_URL = location.href.split("#")[0];
  const DETALLE_URL = new URL("DetallePoliza.aspx", LISTA_URL).href;
  const charset = (document.characterSet || "utf-8").toLowerCase();

  // ---------- panel ----------
  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;z-index:99999;right:16px;bottom:16px;background:#4b2a7b;color:#fff;" +
    "font:14px Arial;padding:12px 16px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:360px";
  document.body.appendChild(panel);
  let cancelado = false;
  const say = (html) => {
    panel.innerHTML = "<b>Vive Seguro · descarga de cartera (v8)</b><br>" + html +
      '<br><a href="#" id="vs_stop" style="color:#ffd27a">Detener y descargar lo que lleva</a>';
    const stop = document.getElementById("vs_stop");
    if (stop) stop.onclick = (e) => { e.preventDefault(); cancelado = true; };
  };

  const gridOf = (doc) => doc && doc.querySelector("table[id$='GVPolList'], table[id*='GVPolList']");
  const textoDe = (doc) => (doc && doc.body && (doc.body.innerText || doc.body.textContent) || "");
  const esDetalleDe = (doc, numero) => {
    const t = textoDe(doc);
    return /consulta de datos generales/i.test(t) && (!numero || t.includes(numero));
  };

  function filas(doc) {
    const grid = gridOf(doc);
    if (!grid) return [];
    const out = [];
    grid.querySelectorAll("tr").forEach((tr) => {
      const tds = tr.querySelectorAll(":scope > td");
      if (tds.length < 5) return;
      const numero = tds[0].textContent.trim().split(/\s+/)[0];
      const a = tds[0].querySelector("a");
      if (!a || !POLIZA.test(numero)) return;
      const m = (a.getAttribute("href") || "").match(/__doPostBack\('([^']+)','([^']*)'\)/);
      out.push({ numero, a, target: m && m[1], arg: m ? m[2] : "",
        celdas: [...tds].slice(0, 5).map((td) => td.textContent.replace(/\s+/g, " ").trim()) });
    });
    return out;
  }

  function paginas(doc) {
    const out = {};
    const grid = gridOf(doc);
    if (!grid) return out;
    grid.querySelectorAll("a[href*='Page$']").forEach((a) => {
      const m = a.getAttribute("href").match(/__doPostBack\('([^']+)','Page\$(\d+)'\)/);
      if (m) out[parseInt(m[2], 10)] = m[1];
    });
    return out;
  }

  // ---------- lectura del formulario: solo etiquetas y valores ----------
  const ETIQUETAS = ["tipo de seguro", "plan", "estatus", "suma asegurada", "moneda", "fecha emision",
    "forma de pago", "medio de cobro", "banco", "numero de token/cuenta clabe", "dia de cobro", "agente",
    "e-mail", "telefono", "contratante", "asegurados", "fecha de nacimiento", "calle y numero", "colonia",
    "ciudad o municipio", "estado", "codigo postal", "pais", "correo electronico", "telefono particular",
    "telefono oficina"];
  const norm = (t) => (t || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/:/g, " ").replace(/\s+/g, " ").trim();
  function lineas(node) {
    const out = [];
    const w = node.ownerDocument.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const t = w.currentNode.nodeValue.replace(/\s+/g, " ").trim();
      if (t) out.push(t);
    }
    return out;
  }
  function extraer(doc) {
    const celdas = [...doc.querySelectorAll("td, th")];
    const idx = {};
    celdas.forEach((c, i) => {
      const k = norm(lineas(c).join(" "));
      if (ETIQUETAS.includes(k) && !(k in idx)) idx[k] = i;
    });
    const esEtiqueta = new Set(Object.values(idx));
    const campos = [];
    for (const k of ETIQUETAS) {
      if (!(k in idx)) continue;
      const i = idx[k];
      let valor = [];
      for (let j = i + 1; j < Math.min(i + 3, celdas.length); j++) {
        if (esEtiqueta.has(j)) break;
        valor = lineas(celdas[j]);
        if (valor.length) break;
      }
      campos.push({ clave: k, etiqueta: lineas(celdas[i]).join(" "), valor });
    }
    return campos;
  }

  // ---------- peticiones en segundo plano (método rápido) ----------
  const decode = async (resp) => {
    const buf = await resp.arrayBuffer();
    const m = (resp.headers.get("content-type") || "").match(/charset=([\w-]+)/i);
    try { return new TextDecoder((m && m[1]) || charset).decode(buf); }
    catch (e) { return new TextDecoder("utf-8").decode(buf); }
  };
  function cuerpo(fromDoc, target, arg) {
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
    return { action: new URL(form.getAttribute("action") || LISTA_URL, LISTA_URL).href, body };
  }
  async function postSinSeguir(fromDoc, target, arg) {
    const { action, body } = cuerpo(fromDoc, target, arg);
    // redirect: "manual" → no seguimos el salto a otro dominio (eso era lo que Chrome bloqueaba)
    const r = await fetch(action, { method: "POST", body, credentials: "include", redirect: "manual",
      headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    return r.type === "opaqueredirect" ? null : parser.parseFromString(await decode(r), "text/html");
  }
  async function detalleRapido(listDoc, fila) {
    const directo = await postSinSeguir(listDoc, fila.target, fila.arg);
    if (directo && esDetalleDe(directo, fila.numero)) return directo;
    const r = await fetch(DETALLE_URL, { credentials: "include", redirect: "manual" });
    if (r.type === "opaqueredirect" || !r.ok) return null;
    const d = parser.parseFromString(await decode(r), "text/html");
    return esDetalleDe(d, fila.numero) ? d : null;
  }

  // ---------- ventana auxiliar (método de respaldo) ----------
  let win = null, marca = 0;
  const docDe = () => { try { return win.document; } catch (e) { return null; } };
  const marcar = () => { const d = docDe(); if (d) d.__vs = ++marca; return marca; };
  async function esperar(cond, ms = ESPERA_MAX_MS) {
    const fin = Date.now() + ms;
    while (Date.now() < fin) {
      if (cancelado || !win || win.closed) return null;
      const d = docDe();
      // «interactive» basta: algunas pólizas tienen imágenes o recursos del portal que nunca terminan de cargar,
      // y el formulario ya está completo aunque la página siga «cargando».
      try {
        if (d && d.readyState !== "loading" && cond(d)) {
          if (d.readyState !== "complete") { await sleep(1200); try { win.stop(); } catch (e) { /* */ } }
          if (docDe() === d) return d;
        }
      } catch (e) { /* cargando */ }
      await sleep(300);
    }
    return null;
  }
  const esperarNuevo = (m, cond, ms) => esperar((d) => d.__vs !== m && cond(d), ms);
  function postbackEn(d, target, arg, a) {
    try { win.open = (u) => { win.location.href = u; return win; }; } catch (e) { /* */ }
    if (target && typeof win.__doPostBack === "function") win.__doPostBack(target, arg || "");
    else if (a) a.click();
  }
  async function abrirAux() {
    if (win && !win.closed) return true;
    win = window.open(LISTA_URL, "vs_smnyl_aux", "width=900,height=700");
    if (!win) return false;
    window.focus();
    return !!(await esperar((d) => filas(d).length > 0));
  }
  async function auxIrAPagina(pg, desdeCero) {
    let d = docDe();
    if (desdeCero || !d || !filas(d).length || !d.__vs_pg) {
      const m = marcar(); win.location.href = LISTA_URL;
      d = await esperarNuevo(m, (x) => filas(x).length > 0);
      if (!d) return null;
      d.__vs_pg = 1;
    }
    // avanza por el paginador hasta la página pedida (de ser necesario, en varios saltos «...»)
    for (let saltos = 0; d && d.__vs_pg !== pg && saltos < 30; saltos++) {
      const pags = paginas(d);
      let destino = pags[pg] ? pg : null;
      if (!destino) {
        const cand = Object.keys(pags).map(Number);
        destino = pg > d.__vs_pg ? Math.max(...cand.filter((n) => n < pg && n > d.__vs_pg))
                                 : Math.min(...cand.filter((n) => n > pg && n < d.__vs_pg));
        if (!isFinite(destino)) return d;
      }
      const m = marcar();
      postbackEn(d, pags[destino], "Page$" + destino);
      d = await esperarNuevo(m, (x) => filas(x).length > 0);
      if (d) d.__vs_pg = destino;
    }
    return d;
  }
  async function detalleAux(numero, pg, desdeCero) {
    let d = await auxIrAPagina(pg, desdeCero);
    let fila = d && filas(d).find((r) => r.numero === numero);
    if (!fila) {
      d = await auxIrAPagina(pg, true);
      fila = d && filas(d).find((r) => r.numero === numero);
    }
    if (!fila) return null;
    let m = marcar();
    postbackEn(d, fila.target, fila.arg, fila.a);
    const det = await esperarNuevo(m, (x) => esDetalleDe(x, numero), desdeCero ? ESPERA_MAX_MS * 2 : ESPERA_MAX_MS);
    const html = det ? extraer(det) : null;
    if (det) {
      // «Regresar» del propio portal → vuelve a la lista
      const btn = det.querySelector("[id$='btnRegresar']");
      m = marcar();
      if (btn) btn.click(); else win.history.back();
      d = await esperarNuevo(m, (x) => filas(x).length > 0, 15000);
      if (d) d.__vs_pg = filas(d).some((r) => r.numero === numero) ? pg : undefined; // si no, se recarga
    }
    return html;
  }

  // ---------- recorrido ----------
  const lista = [], detalles = [], errores = [], filaDe = {};
  let metodo = "ventana", fallosRapido = 0; // en el portal real el método rápido responde error 500
  try {
    if (!gridOf(document)) throw new Error("Abre primero Consultas › Pólizas (la lista) y vuelve a pegar el código.");
    // Fase 1: la lista completa (todas las páginas). Se recorre en la ventana auxiliar, como si dieras clic
    // en 1, 2, 3… al pie de la lista (en el portal real las peticiones en segundo plano dan error 500).
    const paginasDoc = { 1: document };
    const indice = [], vistos = new Set();
    let d = document, pagina = 1;
    if (metodo === "ventana") {
      if (!(await abrirAux())) throw new Error("Chrome bloqueó la ventana auxiliar: clic en el ícono de ventana bloqueada › «Permitir siempre» y vuelve a pegar el código.");
      d = docDe();
      d.__vs_pg = 1;
    }
    while (d && !cancelado) {
      let nuevas = 0;
      for (const r of filas(d)) {
        if (vistos.has(r.numero)) continue;
        vistos.add(r.numero);
        nuevas++;
        lista.push(r.celdas);
        filaDe[r.numero] = r.celdas;
        if (SOLO_VIGENTES && /anulad|cancelad/i.test(r.celdas[4] || "")) continue;
        indice.push({ numero: r.numero, pagina });
      }
      say(`Leyendo la lista: página ${pagina} · ${lista.length} pólizas`);
      if (!nuevas && pagina > 1) break;
      const pags = paginas(d);
      const sig = Object.keys(pags).map(Number).filter((n) => n > pagina).sort((a, b) => a - b)[0];
      if (!sig) break;
      if (metodo === "ventana") {
        const m = marcar();
        postbackEn(d, pags[sig], "Page$" + sig);
        d = await esperarNuevo(m, (x) => filas(x).length > 0);
        if (d) d.__vs_pg = sig;
      } else {
        d = await postSinSeguir(d, pags[sig], "Page$" + sig);
        paginasDoc[sig] = d;
      }
      if (!d || !filas(d).length) {
        console.warn("Vive Seguro: no pude abrir la página", sig, "de la lista");
        break;
      }
      pagina = sig;
    }
    const pedidas = new Set(SOLO_ESTAS.map((n) => String(n).trim().toUpperCase()).filter(Boolean));
    const base = pedidas.size ? indice.filter((x) => pedidas.has(x.numero)) : indice;
    if (pedidas.size) {
      const faltan = [...pedidas].filter((n) => !base.some((x) => x.numero === n));
      if (faltan.length) console.warn("Vive Seguro: no están en la lista del portal:", faltan.join(", "));
    }
    const porHacer = LIMITE && !pedidas.size ? base.slice(0, LIMITE) : base;

    // Fase 2: el formulario de cada póliza
    for (let i = 0; i < porHacer.length && !cancelado; i++) {
      const { numero, pagina: pg } = porHacer[i];
      say(`Póliza ${i + 1} de ${porHacer.length}: ${numero} (método ${metodo})<br>Formularios listos: ${detalles.length} · sin detalle: ${errores.length}`);
      let html = null;
      try {
        if (metodo === "rápido") {
          const listDoc = paginasDoc[pg] || document;
          const fila = filas(listDoc).find((r) => r.numero === numero);
          const det = fila && await detalleRapido(listDoc, fila);
          if (det) html = extraer(det);
          else if (++fallosRapido >= 2 && !detalles.length) {
            metodo = "ventana";
            console.info("Vive Seguro: el método rápido no funcionó en este portal; uso la ventana auxiliar.");
            errores.length = 0;
            i = -1; // vuelve a empezar con la ventana auxiliar
            continue;
          }
        }
        if (!html && metodo === "ventana") {
          if (!(await abrirAux())) {
            say("<b>Chrome bloqueó la ventana auxiliar.</b><br>Clic en el ícono de ventana bloqueada (barra de direcciones) › «Permitir siempre» y vuelve a pegar el código.");
            break;
          }
          html = await detalleAux(numero, pg);
        }
      } catch (e) {
        console.warn("Vive Seguro: error en", numero, e);
      }
      if (html && html.length) detalles.push({ numero, campos: html });
      else {
        errores.push(numero);
        const x = docDe();
        console.warn("Vive Seguro: sin formulario para", numero, "· método", metodo,
          x ? x.location.href + " · " + textoDe(x).slice(0, 300) : "");
      }
      await sleep(PAUSA_MS);
    }

    // Reintentos: las que salieron «sin detalle», empezando desde la lista recién cargada y con más espera
    for (let vuelta = 1; vuelta <= REINTENTOS && errores.length && !cancelado && metodo === "ventana"; vuelta++) {
      const pendientes = errores.splice(0);
      for (let k = 0; k < pendientes.length && !cancelado; k++) {
        const numero = pendientes[k];
        const pg = (porHacer.find((x) => x.numero === numero) || {}).pagina || 1;
        say(`Reintento ${vuelta}: ${k + 1} de ${pendientes.length} · ${numero}<br>Formularios listos: ${detalles.length}`);
        let html = null;
        try { html = await detalleAux(numero, pg, true); } catch (e) { console.warn("Vive Seguro: error en", numero, e); }
        if (html && html.length) detalles.push({ numero, campos: html });
        else {
          errores.push(numero);
          const x = docDe();
          console.warn("Vive Seguro: reintento", vuelta, "sin formulario para", numero,
            x ? x.location.href + " · " + textoDe(x).slice(0, 300) : "");
        }
        await sleep(PAUSA_MS * 2);
      }
    }
  } catch (e) {
    console.warn("Vive Seguro:", e);
    say("<b>" + e.message + "</b>");
  } finally {
    try { if (win) win.close(); } catch (e) { /* */ }
    const esc = (v) => String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
    const _d = new Date();
    const hoy = `${_d.getFullYear()}-${String(_d.getMonth() + 1).padStart(2, "0")}-${String(_d.getDate()).padStart(2, "0")}`;
    // contratante: el portal a veces pone el número de póliza; usamos el de la lista
    const fichas = detalles.map(({ numero, campos }) => {
      const v = {};
      campos.forEach((c) => { v[c.clave] = c; });
      const lista_ = filaDe[numero] || [];
      if (v.contratante && (!v.contratante.valor.length || POLIZA.test(v.contratante.valor.join("")))) {
        v.contratante.valor = lista_[1] ? [lista_[1]] : [];
      }
      return { numero, campos, v, lista: lista_ };
    });
    const txt = (f, k) => (f.v[k] ? f.v[k].valor.join(" ") : "");
    const asegurados = (f) => {
      const n = f.v.asegurados ? f.v.asegurados.valor : [], b = f.v["fecha de nacimiento"] ? f.v["fecha de nacimiento"].valor : [];
      return n.map((x, i) => ({ nombre: x.replace(/^\*\s*/, ""), titular: x.trim().startsWith("*"), nac: b[i] || "" }));
    };
    const COLS = [
      ["Póliza", (f) => f.numero], ["Estatus", (f) => txt(f, "estatus")], ["Tipo de seguro", (f) => txt(f, "tipo de seguro")],
      ["Plan", (f) => txt(f, "plan")], ["Suma asegurada", (f) => txt(f, "suma asegurada")], ["Moneda", (f) => txt(f, "moneda")],
      ["Fecha emisión", (f) => txt(f, "fecha emision")], ["Forma de pago", (f) => txt(f, "forma de pago")],
      ["Medio de cobro", (f) => txt(f, "medio de cobro")], ["Banco", (f) => txt(f, "banco")], ["Día de cobro", (f) => txt(f, "dia de cobro")],
      ["Agente", (f) => txt(f, "agente").replace(/\s*\d{3,}\s*$/, "")],
      ["Clave de agente", (f) => (txt(f, "agente").match(/(\d{3,})\s*$/) || ["", ""])[1]], ["Contratante", (f) => txt(f, "contratante")],
      ["Asegurado principal", (f) => (asegurados(f).find((x) => x.titular) || {}).nombre || f.lista[2] || ""],
      ["Asegurados (* = principal)", (f) => asegurados(f).map((x) => `${x.titular ? "*" : ""}${x.nombre} (${x.nac})`).join("; ")],
      ["Calle y número", (f) => txt(f, "calle y numero")], ["Colonia", (f) => txt(f, "colonia")],
      ["Ciudad o municipio", (f) => txt(f, "ciudad o municipio")], ["Estado", (f) => txt(f, "estado")],
      ["Código postal", (f) => txt(f, "codigo postal")], ["País", (f) => txt(f, "pais")],
      ["Correo electrónico", (f) => txt(f, "correo electronico")], ["Teléfono particular", (f) => txt(f, "telefono particular")],
      ["Teléfono oficina", (f) => txt(f, "telefono oficina")],
    ];
    const csvCelda = (v) => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
    const csv = "\ufeff" + [COLS.map((c) => csvCelda(c[0])).join(",")]
      .concat(fichas.map((f) => COLS.map((c) => csvCelda(c[1](f))).join(","))).join("\r\n");
    const nombre = `vive_seguro_cartera_smnyl_${hoy}.csv`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    a.download = nombre;
    document.body.appendChild(a);
    a.click();
    say(`Listo: ${lista.length} pólizas en la lista, <b>${fichas.length} formularios</b>` +
      (errores.length ? `, ${errores.length} sin detalle (ver consola)` : "") +
      ` · método ${metodo}.<br>Se descargó <b>${nombre}</b>. Ábrelo en Excel o súbelo en Vive Seguro › Pólizas › Importar del portal Seguros Monterrey.`);
    if (errores.length) console.log("Vive Seguro · pólizas sin detalle:", errores.join(", "));
  }
})();
