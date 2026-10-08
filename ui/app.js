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

// One list, from line i: blank lines between items don't end it (models often leave them), it
// keeps its first number (<ol start>), and indented items become a list inside their item.
function listBlock(lines, i, li) {
  const indent = l => l.match(/^\s*/)[0].length;
  const marker = l => l.match(li)[1];
  const ordered = /\d/.test(marker(lines[i]));
  const start = ordered ? parseInt(marker(lines[i]), 10) : 1;
  const items = [];  // {text, sub: {ordered, items: [text]} | null}
  for (; i < lines.length; i++) {
    const l = lines[i];
    if (!l.trim()) {  // a blank line: the list goes on only if an item or an indented line follows
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j < lines.length && (li.test(lines[j]) && (indent(lines[j]) >= 2 || /\d/.test(marker(lines[j])) === ordered)
                               || indent(lines[j]) >= 2)) continue;
      break;
    }
    if (li.test(l) && indent(l) < 2) {
      if (/\d/.test(marker(l)) !== ordered) break;  // a different kind of list starts
      items.push({text: l.replace(li, ""), sub: null});
    } else if (li.test(l) && items.length) {
      const last = items[items.length - 1];
      last.sub = last.sub || {ordered: /\d/.test(marker(l)), items: []};
      last.sub.items.push(l.replace(li, ""));
    } else if (indent(l) >= 2 && items.length) {  // a wrapped line continues the item above it
      const last = items[items.length - 1];
      if (last.sub) last.sub.items[last.sub.items.length - 1] += " " + l.trim();
      else last.text += " " + l.trim();
    } else break;
  }
  const tag = ordered ? "ol" : "ul";
  const html = `<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>` + items.map(it => {
    const sub = it.sub ? `<${it.sub.ordered ? "ol" : "ul"}>` + it.sub.items.map(t => `<li>${inline(t)}</li>`).join("") +
      `</${it.sub.ordered ? "ol" : "ul"}>` : "";
    return `<li>${inline(it.text)}${sub}</li>`;
  }).join("") + `</${tag}>`;
  return [html, i];
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
      const [html, next] = listBlock(lines, i, li);
      out.push(html);
      i = next;
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
      b.setAttribute("aria-label", `${m[1]}: ${NUM_STATUS[n.status]}. Show where it came from.`);
      b.addEventListener("mouseenter", () => popNumber(b, n));
      b.addEventListener("focus", () => popNumber(b, n));
      b.addEventListener("mouseleave", () => hidePop(true));
      b.addEventListener("blur", () => hidePop(true));
      b.addEventListener("click", e => { e.stopPropagation(); popNumber(b, n, true); });
      frag.append(b);
      last = m.index + m[1].length;
    }
    if (!last) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

let popHide = null, popPinned = null;

function numberHtml(n) {
  let h = `<p><strong>${esc(n.number)}</strong> — ${esc(NUM_STATUS[n.status])}.</p>`;
  for (const src of n.sources) {
    const args = Object.entries(src.args || {}).filter(([k]) => k !== "code")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
    h += `<div class="src-item"><code>${esc(src.tool)}</code> <span class="args">${esc(args)}</span>` +
      `<div class="muted">at <code>${esc(src.path)}</code></div>`;
    const ctx = Object.entries(src.context || {});
    if (ctx.length) h += "<dl>" + ctx.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("") + "</dl>";
    h += "</div>";
  }
  for (const q of n.quotes || []) h += `<blockquote>…${esc(q)}…</blockquote>`;
  return h;
}

// a small pop-up beside the number: on hover or keyboard focus; a click (or tap) keeps it open
function popNumber(button, n, pin = false) {
  const pop = $("#numpop");
  clearTimeout(popHide);
  if (pin && popPinned === button) { hidePop(); return; }
  if (pin) popPinned = button;
  pop.innerHTML = numberHtml(n);
  pop.hidden = false;
  const b = button.getBoundingClientRect(), p = pop.getBoundingClientRect();
  const below = b.bottom + 8 + p.height <= innerHeight;
  pop.style.top = `${below ? b.bottom + 8 : Math.max(8, b.top - 8 - p.height)}px`;
  pop.style.left = `${Math.min(Math.max(8, b.left), innerWidth - p.width - 8)}px`;
}

function hidePop(later = false) {
  clearTimeout(popHide);
  const hide = () => { $("#numpop").hidden = true; popPinned = null; };
  if (later) popHide = setTimeout(() => { if (!popPinned) hide(); }, 250);
  else hide();
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
  for (const [i, src] of r.charts.entries()) {
    const a = document.createElement("a");
    a.href = src; a.target = "_blank"; a.rel = "noopener";
    a.addEventListener("click", e => { e.preventDefault(); openViewer(r.charts, i); });
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
  // follow-ups: written by Python from what was looked up; a click puts one in the box to edit
  const fu = $(".followups", res);
  for (const text of r.followups || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = text;
    b.addEventListener("click", () => {
      setMode("chat");
      const q = $("#question");
      q.value = text;
      q.focus();
      q.setSelectionRange(text.length, text.length);
    });
    fu.append(b);
  }
  fu.hidden = !(r.followups || []).length;
  $(".copy", res).addEventListener("click", e => copyAnswer(e.target, r));
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
  $("#send").textContent = "Stop";
  $("#send").classList.add("stop");
  $("#send").title = "Stop this question (nothing is saved)";
  $("#intro").hidden = true;
  const card = $("#answer-tpl").content.firstElementChild.cloneNode(true);
  $(".q", card).textContent = question;
  const steps = $(".progress", card);
  const stages = stagesList(), state = {tools: 0, waitUntil: 0};
  steps.before(stages);
  const working = document.createElement("li");
  working.className = "step working";
  working.textContent = "Working…";
  steps.append(working);
  $("#log").append(card);
  card.scrollIntoView({behavior: "smooth", block: "end"});
  const t0 = Date.now();
  const timer = setInterval(() => {
    const wait = Math.round((state.waitUntil - Date.now()) / 1000);
    working.textContent = wait > 0 ? `Waiting for Groq's free-tier limit: ${wait}s` :
      `Working… ${Math.round((Date.now() - t0) / 1000)}s`;
  }, 1000);
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
        if (ev.kind === "done" && ev.stopped) {
          working.remove();
          stages.remove();
          card.classList.add("stopped");
          const p = document.createElement("p");
          p.className = "muted";
          p.textContent = `Stopped after ${ev.seconds}s — nothing was saved, and follow-ups won't remember it.`;
          $(".a", card).append(p);
          loadUsage();
        } else if (ev.kind === "done") {
          working.remove(); stages.remove(); showResult(card, ev); loadUsage();
          if (!$("#sessions").hidden) loadSessions();
        } else if (ev.kind === "error") throw new Error(ev.text);
        else { updateStages(stages, ev, state); addStep(steps, ev); steps.append(working); }
      }
    }
    if (working.isConnected) throw new Error("The connection closed before the answer arrived.");
  } catch (e) {
    working.remove();
    stages.remove();
    const err = document.createElement("p");
    err.className = "err";
    err.textContent = "Something went wrong: " + e.message;
    $(".a", card).append(err);
  } finally {
    clearInterval(timer);
    busy = false;
    const send = $("#send");
    send.disabled = false;
    send.textContent = "Ask";
    send.classList.remove("stop");
    send.title = "";
    $("#question").focus();
  }
}

// ------------------------------------------------------------ copy, chart viewer, stop, progress
async function copyAnswer(button, r) {
  const sources = (r.tools || []).map(t => `- ${t.summary || t.name}`);
  const text = r.answer + (sources.length ? "\n\nLooked up:\n" + sources.join("\n") : "") +
    (r.unverified?.length ? `\n\nNot found in any tool result: ${r.unverified.join(", ")}` : "");
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {  // older browsers: the hidden-textarea way
    const t = document.createElement("textarea");
    t.value = text;
    document.body.append(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }
  button.textContent = "Copied ✓";
  setTimeout(() => { button.textContent = "Copy answer"; }, 1500);
}

let viewer = {list: [], i: 0, from: null};

function openViewer(list, i) {
  viewer = {list, i, from: document.activeElement};
  $("#viewer").hidden = false;
  showChart(0);
  $("#viewer .v-close").focus();
}

function showChart(step) {
  const v = viewer, n = v.list.length;
  v.i = (v.i + step + n) % n;
  const src = v.list[v.i];
  $("#viewer img").src = src;
  $("#viewer img").alt = "Chart: " + src.split("/").pop().replace(/^\d+-\d+_\d+_/, "").replace(/\.\w+$/, "");
  $("#viewer .v-open").href = src;
  $("#viewer .v-count").textContent = n > 1 ? `${v.i + 1} of ${n} · ` : "";
  $("#viewer .v-prev").hidden = $("#viewer .v-next").hidden = n < 2;
}

function closeViewer() {
  $("#viewer").hidden = true;
  viewer.from?.focus();
}

async function stopQuestion() {
  $("#send").disabled = true;
  $("#send").textContent = "Stopping…";
  try { await fetch("/api/stop", {method: "POST"}); } catch (e) { /* the answer may finish anyway */ }
}

// The checklist above the live steps: what stage the question is at
const STAGES = [["look", "Looking things up"], ["check", "Fact-checking the answer"], ["charts", "Drawing charts"]];

function stagesList() {
  const ol = document.createElement("ol");
  ol.className = "stages";
  ol.innerHTML = STAGES.map(([k, label]) => `<li data-stage="${k}">${label}<span class="count"></span></li>`).join("");
  ol.firstElementChild.classList.add("active");
  return ol;
}

function updateStages(ol, ev, state) {
  const set = (k, cls) => {
    const li = $(`[data-stage=${k}]`, ol);
    li.classList.remove("active", "done", "fixing");
    if (cls) li.classList.add(cls);
  };
  if (ev.kind === "tool") {
    state.tools += 1;
    $("[data-stage=look] .count", ol).textContent = ` (${state.tools} so far)`;
  } else if (ev.kind === "revision") {
    set("check", "fixing");
    $("[data-stage=check]", ol).firstChild.textContent = "Fact-check found a problem: asking for a fix";
  } else if (ev.kind === "answer") {
    set("look", "done");
    set("check", "done");
    $("[data-stage=check]", ol).firstChild.textContent = "Fact-checked";
    set("charts", "active");
  } else if (ev.kind === "wait" && ev.seconds) {
    state.waitUntil = Date.now() + ev.seconds * 1000;
  }
}

// ------------------------------------------------------------ past reports (saved in sessions.py)
const REPORT_KINDS = {compare: "Comparison", brief: "Country brief", poverty: "Poverty profile", works: "What works"};

async function loadReports() {
  try {
    const r = await (await fetch("/api/reports")).json();
    const box = $("#past-reports");
    box.hidden = !r.saving || !r.reports.length;
    $("#reports-saved").innerHTML = r.reports.map(p =>
      `<li data-id="${p.id}"><button type="button" class="open"><span class="title">${esc(p.title)}</span>` +
      `<span class="when">${esc(REPORT_KINDS[p.kind] || p.kind)} · ${esc(p.created.replace("T", " ").slice(0, 16))}</span></button>` +
      `<button type="button" class="act del" title="Delete this report">${ICONS.del}</button></li>`).join("");
  } catch (e) { /* the page still works */ }
}

async function openReport(id) {
  const res = await fetch(`/api/reports/view?id=${id}`);
  const r = await res.json();
  if (!res.ok) { alert(r.error || res.statusText); return; }
  const card = document.createElement("article");
  card.className = "a report";
  $("#report-list").prepend(card);
  renderReport(card, r);
  card.scrollIntoView({behavior: "smooth", block: "start"});
}

async function deleteReport(id, title) {
  if (!confirm(`Delete the saved report "${title}"? This can't be undone.`)) return;
  const res = await fetch("/api/reports/delete", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})});
  if (!res.ok) alert((await res.json()).error || res.statusText);
  loadReports();
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
      if (ev.kind === "done") { finished = true; renderReport(card, ev); loadUsage(); loadReports(); }
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
  if (mode === "reports") { loadReportOptions(); loadReports(); }
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
    const k = n => n >= 1000 ? `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}K` : String(n);
    el.textContent = `Groq ${k(u.groq)} / ${k(u.limit)} today` + (u.openrouter ? ` · backup ${k(u.openrouter)}` : "");
    el.title = `Groq tokens used in the last 24 hours: ${u.groq.toLocaleString()} of ${u.limit.toLocaleString()}` +
      (u.openrouter ? `; OpenRouter backup: ${u.openrouter.toLocaleString()}` : "") +
      ". Counted from the replies this copy received; other programs using the same key aren't included.";
    el.classList.toggle("near", u.groq >= 0.8 * u.limit);
    el.hidden = false;
  } catch (e) { /* the page still works */ }
}

// ------------------------------------------------------------ past chats (sessions.py)
const SVG = d => `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICONS = {
  pin: SVG('<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z"/>'),
  rename: SVG('<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>'),
  export: SVG('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
  del: SVG('<path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>'),
};
let sessionsQuery = "", sessionsTimer = null;

function marked(text, query) {  // text with the search words highlighted
  if (!query) return esc(text);
  const at = text.toLowerCase().indexOf(query.toLowerCase());
  if (at < 0) return esc(text);
  return esc(text.slice(0, at)) + `<mark>${esc(text.slice(at, at + query.length))}</mark>` + esc(text.slice(at + query.length));
}

async function loadSessions() {
  try {
    const q = sessionsQuery;
    const r = await (await fetch("/api/sessions?q=" + encodeURIComponent(q))).json();
    if (q !== sessionsQuery) return;  // a newer search is on its way
    const list = $("#sessions-list");
    if (!r.saving) { list.innerHTML = '<li class="muted">Saving is off on this computer.</li>'; return; }
    if (!r.sessions.length) {
      list.innerHTML = `<li class="muted">${q ? "No chats mention that." : "No saved chats yet."}</li>`;
      return;
    }
    const item = s =>
      `<li class="${s.id === r.current ? "current" : ""}${s.pinned ? " pinned" : ""}" data-id="${s.id}">` +
      `<button type="button" class="open"><span class="title">${marked(s.title, q)}</span>` +
      (s.snippet ? `<span class="snippet">${marked(s.snippet, q)}</span>` : "") +
      `<span class="when">${esc(s.updated.replace("T", " ").slice(0, 16))} · ${s.answers} answer${s.answers === 1 ? "" : "s"}</span></button>` +
      `<span class="acts">` +
      `<button type="button" class="act pin${s.pinned ? " on" : ""}" title="${s.pinned ? "Unpin" : "Pin to the top"}" aria-pressed="${s.pinned}">${ICONS.pin}</button>` +
      `<button type="button" class="act rename" title="Rename">${ICONS.rename}</button>` +
      `<a class="act" href="/api/sessions/export?id=${s.id}" download title="Export (zip)">${ICONS.export}</a>` +
      `<button type="button" class="act del" title="Delete">${ICONS.del}</button></span></li>`;
    const pinned = r.sessions.filter(s => s.pinned), rest = r.sessions.filter(s => !s.pinned);
    list.innerHTML = (pinned.length ? '<li class="group">Pinned</li>' + pinned.map(item).join("") +
                                      (rest.length ? '<li class="group">Recent</li>' : "") : "") + rest.map(item).join("");
  } catch (e) { /* the page still works */ }
}

async function sessionAction(path, body) {
  const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!res.ok) alert((await res.json()).error || res.statusText);
  loadSessions();
}

function startRename(li) {
  const title = $(".open .title", li).textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "rename-box";
  input.value = title;
  input.setAttribute("aria-label", "New name for this chat");
  $(".open", li).replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = save => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (save && name && name !== title) sessionAction("/api/sessions/rename", {id: Number(li.dataset.id), title: name});
    else loadSessions();
  };
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
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
  for (const p of ["setup", "glossary", "keys"]) $("#" + p).hidden = p === id ? !$("#" + p).hidden : true;
}

// ------------------------------------------------------------ settings menu (theme.js applies the look)
function showMenu(show) {
  $("#menu").hidden = !show;
  $("#menu-btn").setAttribute("aria-expanded", String(show));
}

function markLook() {
  const root = document.documentElement.dataset;
  const mark = (attr, value) => document.querySelectorAll(`#menu [${attr}]`).forEach(b => {
    const on = b.getAttribute(attr) === value;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", String(on));
  });
  mark("data-palette", root.palette);
  mark("data-theme-mode", root.themeMode);
  mark("data-text-size", root.size);
}

function setLook(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private window: this visit only */ }
  const root = document.documentElement;
  root.dataset[{palette: "palette", themeMode: "themeMode", textSize: "size"}[key]] = value;
  root.classList.toggle("dark", root.dataset.themeMode === "dark" ||
    (root.dataset.themeMode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches));
  markLook();
}

// ------------------------------------------------------------ keyboard shortcuts
function typingIn(el) {
  return el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

function shortcuts(e) {
  const mod = e.metaKey || e.ctrlKey;
  if (e.key === "/" && !mod && !typingIn(document.activeElement)) {
    e.preventDefault();
    setMode("chat");
    $("#question").focus();
  } else if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    newChat();
  } else if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    showSessions($("#sessions").hidden);
  } else if (e.key === "Escape" && !$("#menu").hidden) {
    showMenu(false);
    $("#menu-btn").focus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadGlossary();
  loadStatus();
  loadUsage();
  const q = $("#question");
  $("#ask").addEventListener("submit", e => {
    e.preventDefault();
    if (busy) { if (e.submitter === $("#send")) stopQuestion(); return; }  // the button reads "Stop"
    const v = q.value; q.value = ""; ask(v);
  });
  q.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ask").requestSubmit(); }
  });
  $("#examples").addEventListener("click", e => { if (e.target.tagName === "BUTTON") ask(e.target.textContent); });
  $("#glossary-btn").addEventListener("click", () => { showMenu(false); toggle("glossary"); });
  $("#setup-btn").addEventListener("click", () => { showMenu(false); toggle("setup"); });
  $("#keys-btn").addEventListener("click", () => { showMenu(false); toggle("keys"); });
  $("#menu-btn").addEventListener("click", e => { e.stopPropagation(); showMenu($("#menu").hidden); });
  document.addEventListener("click", e => { if (!$("#menu").hidden && !e.target.closest(".menu-wrap")) showMenu(false); });
  $("#menu").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (b?.dataset.palette) setLook("palette", b.dataset.palette);
    else if (b?.dataset.themeMode) setLook("themeMode", b.dataset.themeMode);
    else if (b?.dataset.textSize) setLook("textSize", b.dataset.textSize);
  });
  markLook();
  document.addEventListener("keydown", shortcuts);
  $("#viewer .v-close").addEventListener("click", closeViewer);
  $("#viewer .v-prev").addEventListener("click", () => showChart(-1));
  $("#viewer .v-next").addEventListener("click", () => showChart(1));
  $("#viewer").addEventListener("click", e => { if (e.target.id === "viewer") closeViewer(); });
  document.addEventListener("keydown", e => {
    if ($("#viewer").hidden) return;
    if (e.key === "Escape") closeViewer();
    else if (e.key === "ArrowLeft") showChart(-1);
    else if (e.key === "ArrowRight") showChart(1);
  });
  $("#numpop").addEventListener("mouseenter", () => clearTimeout(popHide));
  $("#numpop").addEventListener("mouseleave", () => hidePop(true));
  document.addEventListener("click", e => { if (popPinned && !e.target.closest("#numpop")) hidePop(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#numpop").hidden) hidePop(); });
  window.addEventListener("scroll", () => { if (!popPinned) hidePop(); }, {passive: true});
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
    const li = b.closest("li"), id = Number(li.dataset.id);
    if (b.classList.contains("del")) deleteSession(id, $(".open .title", li).textContent, li.classList.contains("current"));
    else if (b.classList.contains("pin")) sessionAction("/api/sessions/pin", {id, pinned: !li.classList.contains("pinned")});
    else if (b.classList.contains("rename")) startRename(li);
    else if (b.classList.contains("open")) openSession(id);
  });
  $("#reports-saved").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    const li = b.closest("li");
    if (b.classList.contains("del")) deleteReport(Number(li.dataset.id), $(".title", li).textContent);
    else openReport(Number(li.dataset.id));
  });
  $("#sessions-search").addEventListener("input", e => {
    clearTimeout(sessionsTimer);
    sessionsTimer = setTimeout(() => { sessionsQuery = e.target.value.trim(); loadSessions(); }, 200);
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
