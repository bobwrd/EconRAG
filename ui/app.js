"use strict";
// The page for web.py. No libraries: a small markdown renderer, glossary
// highlighting, and a reader for the answer's progress stream (one JSON per line).

const $ = (sel, root = document) => root.querySelector(sel);
let glossary = [];
let busy = false;

// ------------------------------------------------------------ markdown (escape first, then format)
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function cells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
}

function markdown(text) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const code = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { const n = Math.min(h[1].length + 2, 6); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(line);
      const rows = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(cells(lines[i]));
      const numeric = v => /^[-−+]?[$€£]?[\d.,]+%?\s*\w{0,3}$/.test(v);
      out.push('<div class="table"><table><thead><tr>' + head.map(c => `<th>${inline(c)}</th>`).join("") +
        "</tr></thead><tbody>" + rows.map(r => "<tr>" + r.map(c =>
          `<td${numeric(c) ? ' class="num"' : ""}>${inline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table></div>");
      continue;
    }
    const li = /^\s*([-*+]|\d+[.)])\s+/;
    if (li.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items = [];
      for (; i < lines.length && (li.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length)); i++) {
        if (li.test(lines[i])) items.push(lines[i].replace(li, ""));
        else items[items.length - 1] += " " + lines[i].trim();
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>` + items.map(t => `<li>${inline(t)}</li>`).join("") + `</${tag}>`);
      continue;
    }
    const para = [];
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*\|)/.test(lines[i]) && !li.test(lines[i]); i++) {
      para.push(lines[i].trim());
    }
    if (!para.length) { para.push(lines[i].trim()); i++; }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

// ------------------------------------------------------------ glossary
async function loadGlossary() {
  glossary = await (await fetch("/glossary.json")).json();
  const list = $("#glossary-list");
  for (const g of glossary) {
    const dt = document.createElement("dt");
    dt.textContent = g.term;
    const dd = document.createElement("dd");
    dd.textContent = g.definition;
    list.append(dt, dd);
  }
}

// Underlines the first mention of each glossary term; hover or tap shows the definition.
// ------------------------------------------------------------ evidence inspector: click a number
const NUM_STATUS = {
  found: "found in what the tools returned",
  computed: "worked out from other numbers in the answer (a difference, ratio or span); the fact-check accepts it",
  "not found": "not found in any tool result — treat with caution",
};

function markNumbers(root, numbers) {
  const known = numbers.filter(n => n.number);
  if (!known.length) return;
  const byText = new Map(known.map((n, i) => [n.number, i]));
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp("(?<![\\d.,])(" + [...byText.keys()].sort((a, b) => b.length - a.length).map(reEsc).join("|") + ")(?!\\d)", "g");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code, pre, a, .numref") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    let last = 0, m;
    const frag = document.createDocumentFragment();
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      frag.append(text.slice(last, m.index));
      const n = known[byText.get(m[1])];
      const b = document.createElement("button");
      b.type = "button";
      b.className = "numref s-" + n.status.replace(" ", "-");
      b.textContent = m[1];
      b.title = `${m[1]}: ${NUM_STATUS[n.status]}. Click to see where it came from.`;
      b.addEventListener("click", () => showNumber(root, b, n));
      frag.append(b);
      last = m.index + m[1].length;
    }
    if (!last) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

function showNumber(root, button, n) {
  let box = root.nextElementSibling?.classList.contains("num-detail") ? root.nextElementSibling : null;
  if (!box) {
    box = document.createElement("div");
    box.className = "num-detail";
    root.after(box);
  }
  if (box.dataset.number === n.number && !box.hidden) { box.hidden = true; return; }
  box.dataset.number = n.number;
  let h = `<p><strong>${esc(n.number)}</strong> — ${esc(NUM_STATUS[n.status])}.</p>`;
  for (const s of n.sources) {
    const args = Object.entries(s.args || {}).filter(([k]) => k !== "code")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
    h += `<div class="src-item"><code>${esc(s.tool)}</code> <span class="args">${esc(args)}</span>` +
      `<div class="muted">at <code>${esc(s.path)}</code></div>`;
    const ctx = Object.entries(s.context || {});
    if (ctx.length) h += "<dl>" + ctx.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("") + "</dl>";
    h += "</div>";
  }
  for (const q of n.quotes || []) h += `<blockquote>…${esc(q)}…</blockquote>`;
  box.innerHTML = h + '<button type="button" class="ghost close">Close</button>';
  $(".close", box).addEventListener("click", () => { box.hidden = true; button.focus(); });
  box.hidden = false;
}

function highlightTerms(root) {
  if (!glossary.length) return;
  const patterns = [];
  glossary.forEach((g, gi) => g.match.forEach(m => patterns.push({m, gi})));
  patterns.sort((a, b) => b.m.length - a.m.length);
  const isAcronym = m => /^[A-Z$][A-Z0-9$]+s?$/.test(m);
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp("(?<![\\w-])(" + patterns.map(p => reEsc(p.m)).join("|") + ")(?![\\w-])", "gi");
  const used = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest("code, pre, a, .term, th") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    let last = 0, m;
    const frag = document.createDocumentFragment();
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      const p = patterns.find(p => p.m.toLowerCase() === m[1].toLowerCase());
      if (!p || used.has(p.gi) || (isAcronym(p.m) && m[1] !== p.m)) continue;
      used.add(p.gi);
      frag.append(text.slice(last, m.index));
      const span = document.createElement("span");
      span.className = "term";
      span.tabIndex = 0;
      span.textContent = m[1];
      span.dataset.g = p.gi;
      frag.append(span);
      last = m.index + m[1].length;
    }
    if (last) { frag.append(text.slice(last)); node.replaceWith(frag); }
  }
}

function showTip(el) {
  const g = glossary[+el.dataset.g];
  const tip = $("#tip");
  tip.innerHTML = `<strong>${esc(g.term)}</strong><br>${esc(g.definition)}`;
  tip.hidden = false;
  const r = el.getBoundingClientRect();
  const w = Math.min(340, window.innerWidth - 32);
  tip.style.width = w + "px";
  tip.style.left = Math.max(16, Math.min(r.left, window.innerWidth - w - 16)) + "px";
  const below = r.bottom + 8 + tip.offsetHeight < window.innerHeight;
  tip.style.top = (below ? r.bottom + 8 : r.top - tip.offsetHeight - 8) + "px";
}
const hideTip = () => { $("#tip").hidden = true; };
document.addEventListener("mouseover", e => { const t = e.target.closest(".term"); if (t) showTip(t); });
document.addEventListener("mouseout", e => { if (e.target.closest(".term")) hideTip(); });
document.addEventListener("focusin", e => { const t = e.target.closest(".term"); t ? showTip(t) : hideTip(); });
document.addEventListener("click", e => { const t = e.target.closest(".term"); t ? showTip(t) : hideTip(); });
window.addEventListener("scroll", hideTip, true);

// ------------------------------------------------------------ one question
const STEP_ICON = {tool: "→", wait: "⏳", revision: "✎", notice: "ℹ", unverified: "⚠", chart: "▦"};

function addStep(list, ev) {
  if (!(ev.kind in STEP_ICON)) return;
  const li = document.createElement("li");
  li.className = "step " + ev.kind;
  if (ev.kind === "tool") {
    const args = Object.entries(ev.args || {}).filter(([k]) => k !== "code")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
    li.innerHTML = `<span class="ico">${STEP_ICON.tool}</span><code>${esc(ev.name)}</code>` +
      `<span class="args">${esc(args)}</span><div class="sum">${esc(ev.summary || "")}</div>`;
  } else if (ev.kind === "chart") {
    return;  // shown with the answer
  } else if (ev.kind === "revision") {
    li.innerHTML = `<span class="ico">${STEP_ICON.revision}</span>Fact-check found unsupported items ` +
      `(${esc((ev.items || []).join(", "))}) — asking for a revision`;
  } else {
    li.innerHTML = `<span class="ico">${STEP_ICON[ev.kind]}</span>${esc(ev.text)}`;
  }
  list.append(li);
}

function badge(el, result) {
  if (result.backend === "offline") {
    el.className = "badge warn";
    el.textContent = "Offline model — not fact-checked";
  } else if (result.unverified.length) {
    el.className = "badge warn";
    el.textContent = `⚠ ${result.unverified.length} item${result.unverified.length > 1 ? "s" : ""} not verified`;
  } else if (!result.tools.length) {
    el.className = "badge neutral";
    el.textContent = "No data looked up";
  } else {
    el.className = "badge ok";
    el.textContent = "✓ Numbers and citations found in the sources" + (result.revised ? " (after one revision)" : "");
    el.title = "Checked automatically: every number and every cited paper appears in what the tools returned. " +
      "How findings are described, and what is attributed to which paper, is not checked: read critically.";
    const note = document.createElement("span");
    note.className = "muted badge-note";
    note.textContent = "Wording and interpretation aren't checked.";
    el.after(note);
  }
}

function technicalItems(root, items) {
  if (!items.length) { root.innerHTML = '<p class="muted">This answer used no data tools.</p>'; return; }
  root.innerHTML = items.map(it => {
    let h = `<section class="tech"><h4>${esc(it.title)}</h4>`;
    if (it.source) h += `<p class="src">${esc(it.source)}</p>`;
    if (it.definition) h += `<p><strong>Definition.</strong> ${esc(it.definition)}</p>`;
    if (it.units) h += `<p><strong>Units.</strong> ${esc(it.units)}</p>`;
    if (it.values && it.values.length) {
      h += '<div class="table"><table><thead><tr><th>Where / what</th><th>Year</th><th>Value</th></tr></thead><tbody>' +
        it.values.map(v => `<tr><td>${esc(v[0] ?? "")}</td><td>${esc(v[1] ?? "")}</td><td class="num">${esc(v[2] ?? "")}</td></tr>`).join("") +
        "</tbody></table></div>";
    }
    if (it.notes && it.notes.length) h += "<ul>" + it.notes.map(n => `<li>${esc(n)}</li>`).join("") + "</ul>";
    if (it.data && it.data.length) h += "<ul>" + it.data.map(n => `<li><code>${esc(n)}</code></li>`).join("") + "</ul>";
    if (it.code) h += `<details><summary>Script</summary><pre><code>${esc(it.code)}</code></pre></details>`;
    if (it.output) h += `<details open><summary>Output</summary><pre><code>${esc(it.output)}</code></pre></details>`;
    return h + "</section>";
  }).join("");
}

function showResult(card, r) {
  const res = $(".result", card);
  badge($(".badge", res), r);
  if (r.unverified.length) {
    const u = $(".unverified", res);
    u.hidden = false;
    u.textContent = "Not found in any tool result (treat with caution): " + r.unverified.join(", ");
  }
  const plain = $(".plain", res);
  plain.innerHTML = markdown(r.answer);
  highlightTerms(plain);
  markNumbers(plain, r.numbers || []);
  technicalItems($(".tech-items", res), r.technical || []);
  const charts = $(".charts", res);
  for (const src of r.charts) {
    const a = document.createElement("a");
    a.href = src; a.target = "_blank"; a.rel = "noopener";
    const img = document.createElement("img");
    img.src = src; img.alt = "Chart: " + src.split("/").pop().replace(/^\d+-\d+_\d+_/, "").replace(/\.\w+$/, "");
    img.loading = "lazy";
    a.append(img);
    charts.append(a);
  }
  // the live progress becomes the collapsible "what I looked up"
  const trail = $(".trail", res);
  const steps = $(".progress", card);
  $("summary", trail).textContent = r.tools.length
    ? `What I looked up (${r.tools.length} tool call${r.tools.length > 1 ? "s" : ""})` : "What happened";
  $("ul", trail).append(...steps.children);
  steps.remove();
  if (!$("ul", trail).children.length) trail.hidden = true;
  // downloads
  const dl = $(".dl", res), kind = $(".dl-kind", res);
  const setHref = () => { dl.href = `/api/data?id=${r.id}&kind=${kind.value}`; };
  kind.addEventListener("change", setHref);
  setHref();
  if (!r.tools.some(t => t.name !== "search_papers")) $(".foot label", res).hidden = dl.hidden = true;
  const backend = {groq: "gpt-oss-120b on Groq", openrouter: "OpenRouter (backup)", offline: "phi3.5, offline"}[r.backend] || r.backend;
  $(".meta", res).textContent = `${backend} · ${r.seconds}s` + (r.tokens ? ` · ${r.tokens.toLocaleString()} tokens` : "");
  // plain / technical
  for (const b of res.querySelectorAll(".seg button")) {
    b.addEventListener("click", () => {
      res.querySelectorAll(".seg button").forEach(x => x.classList.toggle("on", x === b));
      $(".plain", res).hidden = b.dataset.view !== "plain";
      $(".technical", res).hidden = b.dataset.view !== "technical";
    });
  }
  if (r.backend === "offline" || !r.tools.length) $(".rewrite", res).hidden = true;
  $(".rewrite-btn", res).addEventListener("click", e => rewrite(e.target, r.id, $(".rewrite-out", res)));
  res.hidden = false;
}

async function rewrite(btn, id, out) {
  if (busy) { out.innerHTML = '<p class="err">Wait for the current question to finish.</p>'; return; }
  btn.disabled = true;
  btn.textContent = "Rewriting…";
  try {
    const res = await fetch("/api/technical", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id})});
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || res.statusText);
    const flag = r.unverified.length
      ? `<p class="badge warn">⚠ Not found in any tool result: ${esc(r.unverified.join(", "))}</p>`
      : '<p class="badge ok">✓ Numbers and citations found in the sources</p>';
    out.innerHTML = flag + markdown(r.text) + `<p class="muted">${(r.tokens || 0).toLocaleString()} tokens</p>`;
    highlightTerms(out);
    loadUsage();
    btn.hidden = true;
    btn.nextElementSibling.hidden = true;
  } catch (e) {
    out.innerHTML = `<p class="err">${esc(e.message)}</p>`;
    btn.disabled = false;
    btn.textContent = "Rewrite this answer technically";
  }
}

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  $("#send").disabled = true;
  $("#intro").hidden = true;
  const card = $("#answer-tpl").content.firstElementChild.cloneNode(true);
  $(".q", card).textContent = question;
  const steps = $(".progress", card);
  const working = document.createElement("li");
  working.className = "step working";
  working.textContent = "Working…";
  steps.append(working);
  $("#log").append(card);
  card.scrollIntoView({behavior: "smooth", block: "end"});
  const t0 = Date.now();
  const timer = setInterval(() => { working.textContent = `Working… ${Math.round((Date.now() - t0) / 1000)}s`; }, 1000);
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question})});
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", done = false;
    while (!done) {
      const chunk = await reader.read();
      done = chunk.done;
      buf += decoder.decode(chunk.value || new Uint8Array(), {stream: !done});
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.kind === "done") { working.remove(); showResult(card, ev); loadUsage(); if (!$("#sessions").hidden) loadSessions(); }
        else if (ev.kind === "error") throw new Error(ev.text);
        else { addStep(steps, ev); steps.append(working); }
      }
    }
    if (working.isConnected) throw new Error("The connection closed before the answer arrived.");
  } catch (e) {
    working.remove();
    const err = document.createElement("p");
    err.className = "err";
    err.textContent = "Something went wrong: " + e.message;
    $(".a", card).append(err);
  } finally {
    clearInterval(timer);
    busy = false;
    $("#send").disabled = false;
    $("#question").focus();
  }
}

// ------------------------------------------------------------ reports
async function readStream(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "", done = false;
  while (!done) {
    const chunk = await reader.read();
    done = chunk.done;
    buf += decoder.decode(chunk.value || new Uint8Array(), {stream: !done});
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

const FORMAT_LABELS = {pdf: "PDF", docx: "Word", md: "Markdown (zip)", tex: "LaTeX (zip)", bib: "BibTeX", json: "JSON", csv: "Tables (CSV zip)", ipynb: "Jupyter notebook", data: "Data (zip)"};

function tableHtml(t) {
  return '<div class="table"><table><thead><tr>' + t.columns.map(c => `<th>${esc(c)}</th>`).join("") +
    "</tr></thead><tbody>" + t.rows.map(r => "<tr>" + r.map((c, i) =>
      `<td${i ? ' class="num"' : ""}>${esc(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>";
}

function renderReport(card, r) {
  const s = r.summary;
  const badgeHtml = s.status === "ok"
    ? '<span class="badge ok">✓ Every number in the summary is in the data</span><span class="muted badge-note">Wording isn\'t checked.</span>'
    : s.status === "unverified"
      ? `<span class="badge warn">⚠ Not found in the data: ${esc(s.unverified.join(", "))}</span>`
      : s.status === "python"
        ? `<span class="badge neutral">Highlights listed by Python (no model, no tokens)${s.reason ? " — model unavailable" : ""}</span>`
        : `<span class="badge neutral">No written summary: ${esc(s.reason || "model unavailable")}</span>`;
  let h = `<h2 class="r-title">${esc(r.title)}</h2><p class="muted">Made ${esc(r.created.replace("T", " "))}` +
    (s.tokens ? ` · ${s.tokens.toLocaleString()} tokens` : "") + "</p>";
  h += '<div class="downloads">' + Object.keys(r.formats).map(f =>
    `<a class="button" href="/api/report?id=${r.id}&fmt=${f}" download>${FORMAT_LABELS[f] || f}</a>`).join("") + "</div>";
  h += `<div class="bar">${badgeHtml}</div>`;
  if (s.text) h += `<div class="md r-summary">${markdown(s.text)}</div>`;
  for (const sec of r.sections) {
    h += `<section class="r-section"><h3>${esc(sec.heading)}</h3>${sec.table ? tableHtml(sec.table) : ""}`;
    if (sec.items && sec.items.length) h += '<ul class="items">' + sec.items.map(i => "<li><strong>" +
      (i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a>` : esc(i.title)) +
      `</strong> — ${esc(i.text)}</li>`).join("") + "</ul>";
    if (sec.notes.length) h += "<ul class=\"notes\">" + sec.notes.map(n => `<li>${esc(n)}</li>`).join("") + "</ul>";
    if (sec.charts.length) h += '<div class="charts">' + sec.charts.map(c =>
      `<a href="${c}" target="_blank" rel="noopener"><img src="${c}" alt="Chart for ${esc(sec.heading)}" loading="lazy"></a>`).join("") + "</div>";
    h += "</section>";
  }
  h += "<h3>About this report</h3><ul class=\"notes\">" + r.about.map(a => `<li>${esc(a)}</li>`).join("") + "</ul>";
  h += "<h3>Sources</h3>" + r.sources.map(src => `<p class="muted">${esc((src.author || "").replace(/[{}]/g, "").replace(/ and /g, ", "))}. ` +
    `<em>${esc(src.title)}</em>${src.journal ? ". " + esc(src.journal) : ""}${src.year && !src.accessed ? ", " + esc(src.year) : ""}. ` +
    (src.url ? `<a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.url)}</a>` : "") +
    `${src.accessed ? " (accessed " + esc(src.accessed) + ")" : ""}. ${esc(src.note || "")}</p>`).join("");
  card.innerHTML = h;
  const summary = $(".r-summary", card);
  if (summary) highlightTerms(summary);
}

const REPORT_COST = {compare: "~2–5K", brief: "~3–5K", poverty: "~2–4K", works: "~4–6K"};

function reportKind() {
  return document.querySelector("input[name=r-kind]:checked").value;
}

function showReportKind() {
  const kind = reportKind();
  for (const el of document.querySelectorAll("#report-form [data-kind]")) el.hidden = !el.dataset.kind.split(" ").includes(kind);
  $("#r-cost").textContent = `(${REPORT_COST[kind]} Groq tokens, up to twice that if the fact-check asks for a fix; ` +
    "unticked: Python lists the highlights, free)";
  try { localStorage.setItem("reportKind", kind); } catch (e) { /* private window */ }
}

function reportBody() {
  const kind = reportKind(), summary = $("#r-summary").checked;
  const list = id => $(id).value.split(",").map(x => x.trim()).filter(Boolean);
  const years = {start: $("#r-start").value || null, end: $("#r-end").value || null};
  if (kind === "compare") {
    const topics = [...document.querySelectorAll("#r-topics input:checked")].map(i => i.value);
    return {kind, countries: $("#r-countries").value, topics, summary, indicators: list("#r-indicators"), ...years};
  }
  if (kind === "works") return {kind, intervention: $("#r-intervention").value, region: $("#r-region").value,
    outcome: $("#r-outcome").value, summary};
  if (kind === "brief") return {kind, country: $("#r-country").value, summary, ...years,
    indicators: list("#r-brief-indicators"), peers: $("#r-peers").value};
  return {kind, country: $("#r-country").value, summary};
}

async function makeReport() {
  if (busy) return;
  const body = reportBody();
  const needed = {compare: "countries", brief: "country", poverty: "country", works: "intervention"}[body.kind];
  if (!String(body[needed] || "").trim()) { alert(`Please enter the ${needed}.`); return; }
  busy = true;
  $("#r-make").disabled = true;
  const card = document.createElement("article");
  card.className = "a report";
  const steps = document.createElement("ul");
  steps.className = "progress";
  card.append(steps);
  $("#report-list").prepend(card);
  const add = text => { const li = document.createElement("li"); li.className = "step"; li.textContent = text; steps.append(li); };
  add("Starting…");
  try {
    const res = await fetch("/api/report", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)});
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
    let finished = false;
    await readStream(res, ev => {
      if (ev.kind === "done") { finished = true; renderReport(card, ev); loadUsage(); }
      else if (ev.kind === "error") throw new Error(ev.text);
      else add(ev.text);
    });
    if (!finished) throw new Error("The connection closed before the report was ready.");
  } catch (e) {
    const err = document.createElement("p");
    err.className = "err";
    err.textContent = "Couldn't make the report: " + e.message;
    card.append(err);
  } finally {
    busy = false;
    $("#r-make").disabled = false;
  }
}

// Drop-downs for the report form's short, fixed lists (countries, regions, J-PAL outcomes).
// The text boxes stay if the lists can't be loaded.
let reportOptions = null;

function options(values, first) {
  return `<option value="">${esc(first)}</option>` + values.map(v => `<option>${esc(v)}</option>`).join("");
}

function toSelect(id, html) {
  const input = $("#" + id);
  if (!input || input.tagName === "SELECT") return;
  const select = document.createElement("select");
  select.id = id;
  select.innerHTML = html;
  if (input.value) select.value = input.value;
  input.replaceWith(select);
}

async function loadReportOptions() {
  if (reportOptions) return;
  try {
    const res = await fetch("/api/report-options");
    if (!res.ok) return;
    reportOptions = await res.json();
  } catch (e) { return; }
  const o = reportOptions;
  toSelect("r-country", options(o.countries, "Choose a country…"));
  toSelect("r-region", options([], "Anywhere") +
    `<optgroup label="Regions">${o.regions.map(r => `<option>${esc(r)}</option>`).join("")}</optgroup>` +
    `<optgroup label="Countries">${o.countries.map(c => `<option>${esc(c)}</option>`).join("")}</optgroup>`);
  if (o.outcomes.length) toSelect("r-outcome", options(o.outcomes, "Any outcome"));
  for (const sel of document.querySelectorAll("select.add-to")) {
    sel.innerHTML = options(o.countries, sel.dataset.target === "r-peers" ? "Add a peer country…" : "Add a country…");
    sel.hidden = false;
    sel.addEventListener("change", () => {
      const target = $("#" + sel.dataset.target);
      const have = target.value.split(",").map(x => x.trim()).filter(Boolean);
      if (sel.value && !have.includes(sel.value)) target.value = [...have, sel.value].join(", ");
      sel.value = "";
    });
  }
  $("#r-end").max = $("#r-start").max = new Date().getFullYear();
}

function setMode(mode) {
  if (mode === "reports") loadReportOptions();
  document.querySelectorAll(".modes button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  $("#log").hidden = mode !== "chat";
  $("#ask").hidden = mode !== "chat";
  $("#reset-btn").hidden = mode !== "chat";
  $("#reports-view").hidden = mode !== "reports";
  try { localStorage.setItem("mode", mode); } catch (e) { /* private window */ }
}

// ------------------------------------------------------------ page setup
async function loadStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    $("#local").textContent = `Running on this computer only (${s.listening}) — other computers can't connect.`;
    const items = [
      ["Online analyst (Groq key)", s.groq],
      ["Backup model (OpenRouter key)", s.openrouter],
      [`Paper library (${s.papers} passages)`, s.papers > 0],
      ...Object.entries(s.pieces),
    ];
    $("#setup-list").innerHTML = items.map(([name, on]) =>
      `<li class="${on ? "on" : "off"}">${on ? "✓" : "–"} ${esc(name)}${on ? "" : " <span class=\"muted\">(off)</span>"}</li>`).join("");
    if (!s.groq && !s.offline_model) {
      $("#setup").hidden = false;
    }
    $("#r-topics").innerHTML = Object.entries(s.report_topics).map(([k, label]) =>
      `<label><input type="checkbox" value="${esc(k)}"${s.report_default_topics.includes(k) ? " checked" : ""}> ${esc(label)}</label>`).join("");
  } catch (e) { /* the page still works */ }
}

// Groq's free daily cap, from the token counts in its replies (this copy's requests only)
async function loadUsage() {
  try {
    const u = await (await fetch("/api/usage")).json();
    const el = $("#usage");
    el.textContent = `Groq tokens, last 24 hours: ${u.groq.toLocaleString()} of ${u.limit.toLocaleString()}` +
      (u.openrouter ? ` · OpenRouter backup: ${u.openrouter.toLocaleString()}` : "");
    el.title = "Counted from the replies this copy received; other programs using the same key aren't included.";
    el.classList.toggle("near", u.groq >= 0.8 * u.limit);
    el.hidden = false;
  } catch (e) { /* the page still works */ }
}

// ------------------------------------------------------------ past chats (sessions.py)
async function loadSessions() {
  try {
    const r = await (await fetch("/api/sessions")).json();
    const list = $("#sessions-list");
    if (!r.saving) { list.innerHTML = '<li class="muted">Saving is off on this computer.</li>'; return; }
    if (!r.sessions.length) { list.innerHTML = '<li class="muted">No saved chats yet.</li>'; return; }
    list.innerHTML = r.sessions.map(s =>
      `<li class="${s.id === r.current ? "current" : ""}"><button type="button" class="open" data-id="${s.id}">` +
      `${esc(s.title)}<span class="when">${esc(s.updated.replace("T", " ").slice(0, 16))} · ` +
      `${s.answers} answer${s.answers === 1 ? "" : "s"}</span></button>` +
      `<span class="acts"><a class="ghost" href="/api/sessions/export?id=${s.id}" download>Export</a>` +
      `<button type="button" class="ghost del" data-id="${s.id}" aria-label="Delete this chat">Delete</button></span></li>`).join("");
  } catch (e) { /* the page still works */ }
}

function clearLog(showIntro = false) {
  $("#intro").hidden = !showIntro;
  for (const el of document.querySelectorAll("#log > .turn, #log > .divider")) el.remove();
}

// A new chat: an empty page with the examples, and the server forgets the earlier questions
// (the earlier chat stays saved under Past chats)
async function newChat() {
  if (busy) return;
  await fetch("/api/reset", {method: "POST"});
  clearLog(true);
  setMode("chat");
  if (!$("#sessions").hidden) loadSessions();
  $("#question").focus();
}

async function openSession(id) {
  if (busy) return;
  const res = await fetch("/api/sessions/open", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})});
  const s = await res.json();
  if (!res.ok) { alert(s.error || res.statusText); return; }
  clearLog();
  const note = document.createElement("p");
  note.className = "divider";
  note.textContent = `Saved chat from ${s.created.replace("T", " ").slice(0, 16)} — follow-up questions continue it`;
  $("#log").append(note);
  for (const a of s.answers) {
    const card = $("#answer-tpl").content.firstElementChild.cloneNode(true);
    $(".q", card).textContent = a.question;
    const steps = $(".progress", card);
    for (const ev of a.events || []) addStep(steps, ev);
    $("#log").append(card);
    showResult(card, a);
  }
  setMode("chat");
  if (!WIDE.matches) showSessions(false, false);  // a docked sidebar stays open
  loadSessions();
  $("#log").lastElementChild?.scrollIntoView({block: "start"});
}

async function deleteSession(id, title, current) {
  if (busy || !confirm(`Delete the saved chat "${title}"? This can't be undone.`)) return;
  const res = await fetch("/api/sessions/delete", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})});
  if (!res.ok) { alert((await res.json()).error || res.statusText); return; }
  if (current) clearLog(true);  // the chat on screen was deleted
  loadSessions();
}

// Past chats: a sidebar beside the chat on wide screens (open unless you closed it last time),
// a drawer over it on narrow ones (closed until opened)
const WIDE = window.matchMedia("(min-width: 900px)");

function showSessions(show, remember = true) {
  $("#sessions").hidden = !show;
  $("#sessions-btn").setAttribute("aria-expanded", String(show));
  document.body.classList.toggle("docked", show && WIDE.matches);
  if (remember && WIDE.matches) {
    try { localStorage.setItem("sessionsOpen", show ? "1" : "0"); } catch (e) { /* private window */ }
  }
  if (show) loadSessions();
}

function sidebarDefault() {
  let saved = null;
  try { saved = localStorage.getItem("sessionsOpen"); } catch (e) { /* private window */ }
  showSessions(WIDE.matches && saved !== "0", false);
}

function toggle(id) {
  for (const p of ["setup", "glossary"]) $("#" + p).hidden = p === id ? !$("#" + p).hidden : true;
}

document.addEventListener("DOMContentLoaded", () => {
  loadGlossary();
  loadStatus();
  loadUsage();
  const q = $("#question");
  $("#ask").addEventListener("submit", e => { e.preventDefault(); const v = q.value; q.value = ""; ask(v); });
  q.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ask").requestSubmit(); }
  });
  $("#examples").addEventListener("click", e => { if (e.target.tagName === "BUTTON") ask(e.target.textContent); });
  $("#glossary-btn").addEventListener("click", () => toggle("glossary"));
  $("#setup-btn").addEventListener("click", () => toggle("setup"));
  $("#sessions-btn").addEventListener("click", () => showSessions($("#sessions").hidden));
  $("#sessions-close").addEventListener("click", () => showSessions(false));
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !$("#sessions").hidden && !WIDE.matches) showSessions(false);
  });
  sidebarDefault();
  WIDE.addEventListener("change", sidebarDefault);
  $("#sessions-list").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    const li = b.closest("li");
    if (b.classList.contains("del")) deleteSession(Number(b.dataset.id), li.querySelector(".open").firstChild.textContent,
                                                   li.classList.contains("current"));
    else openSession(Number(b.dataset.id));
  });
  $("#glossary-filter").addEventListener("input", e => {
    const f = e.target.value.toLowerCase();
    for (const dt of $("#glossary-list").querySelectorAll("dt")) {
      const show = !f || dt.textContent.toLowerCase().includes(f) || dt.nextElementSibling.textContent.toLowerCase().includes(f);
      dt.hidden = dt.nextElementSibling.hidden = !show;
    }
  });
  $("#reset-btn").addEventListener("click", newChat);
  document.querySelectorAll(".modes button").forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("#report-form").addEventListener("submit", e => { e.preventDefault(); makeReport(); });
  document.querySelectorAll("input[name=r-kind]").forEach(i => i.addEventListener("change", showReportKind));
  try {
    const saved = localStorage.getItem("reportKind");
    const input = saved && document.querySelector(`input[name=r-kind][value=${saved}]`);
    if (input) input.checked = true;
  } catch (e) { /* private window */ }
  showReportKind();
  let mode = "chat";
  try { mode = localStorage.getItem("mode") || "chat"; } catch (e) { /* private window */ }
  setMode(mode);
  q.focus();
});
