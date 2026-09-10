"use strict";

const EPOCH = Date.UTC(2010, 0, 1), DAY = 86400000, ROWH = 30;
let TODAY = 0;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
const fmtDay = d => new Date(EPOCH + d * DAY).toISOString().slice(0, 10);
const n1 = v => v == null ? "—" : v.toFixed(1);

const SCALE = v => (v + 3.5) * 340;
const k1 = v => v == null ? "—" : String(Math.round(SCALE(v)));
const pct = (n, of) => !n ? "" : `${n} <span class="mut">(${Math.round(100 * n / of)}%)</span>`;

const ptsOf = (n, x) => x ? `${n} / ${x * 3}` : "—";

const aliasOf = i => (D.aliases || {})[i] || [];
const akaFor = key => (D.instAka || {})[key] || [];
function ago(day) {
  const d = Math.max(0, TODAY - day);
  if (d < 45) return d === 0 ? "today" : d + (d === 1 ? " day ago" : " days ago");
  const mo = Math.round(d / 30.44);
  if (mo < 24) return mo + (mo === 1 ? " month ago" : " months ago");
  return Math.round(d / 365.25) + " years ago";
}

let D, P, T;
let C = {}, CV = {};
let JN = [], JC = [], JLINK = [];
let judgeOf = new Map();
const JUDGE_BS_DEFAULT = 1700;
let judgeQ = "", judgeMinBS = JUDGE_BS_DEFAULT;
let judgeSort = { k: "rounds", dir: -1 }, judgeCache = { key: undefined, rows: null };
let derived = [];
let listed = [];
const F = { minT: 7, from: "" };
let sortKey = "val", sortDir = -1, highlight = -1;
let view = { v: "board" };
let tourQ = "", tourSort = { k: "d", dir: -1 }, champs = null, champsRest = false;
const INST_BS_DEFAULT = 1700;
let instQ = "", instMinBS = INST_BS_DEFAULT, instSort = { k: "wins", dir: -1 };
const INST_MINT_DEFAULT = 3;
let instMinT = INST_MINT_DEFAULT, instFrom = "";
let balMinRooms = 15, balMinBS = INST_BS_DEFAULT, balTags = null,
    balSort = { k: "bal", dir: -1 }, balMoQ = "", balTourQ = "";
let instCache = { key: undefined, rows: null };

async function boot() {
  D = await (await fetch("/data.bin")).json();
  TODAY = Math.round((Date.parse(D.built) - EPOCH) / DAY);
  ({ players: P, tournaments: T } = D);
  JN = D.jn || []; JLINK = D.jlink || [];
  JLINK.forEach((p, j) => { if (p >= 0 && !judgeOf.has(p)) judgeOf.set(p, j); });
  buildDerived();
  loadRest();
  $("loading").remove();
  $("app").style.display = "flex";

  const mm = $("main");
  document.documentElement.style.setProperty(
    "--sb", (mm.offsetWidth - mm.clientWidth) + "px");
  wire();
  lastTier = tierNow();
  const r = location.protocol === "file:" ? { v: "board" } : parsePath(location.pathname);
  go(r.v, r.id, false);
}

function buildDerived() {
  const B = D.board || [], IN = D.instNames || {}, AT = D.instAt || [];
  derived = P.map((p, i) => {
    const b = B[i] || [];
    return { i, name: p[0], lname: p[0].toLowerCase(), mu: p[1], sg: p[2], last: p[3],
             amu: p[4], asg: p[5],
             nt: b[0] || 0, nr: b[1] || 0, avg: b[2] ?? null, ppr: b[3] ?? null,
             wins: b[4] || 0, finals: b[5] || 0, breaks: b[6] || 0, obreaks: b[7] || 0,
             inst: b[8] ?? null,
             insts: (b[9] || []).map(k => ({ key: k, name: IN[k] || k })),
             first: b[10] ?? null, instAt: AT[i] || [], results: null };
  });
}
function resultsOf(d) {
  if (d.results) return d.results;
  const car = C[d.i] || [];
  d.results = car.map(e => {
    const rm = T[e[0]].rounds || [];

    let bd = null, bcat = "", won = false, elim = false, open = false,
        obd = null, owon = false, tp = 0, tpr = 0, ts = 0, tns = 0;
    for (const r of e[3]) {
      const meta = rm[r[0]];
      if (!meta) continue;
      if (r[3] != null) { ts += r[3]; tns++; }
      if (meta[1] === "P") {
        if (r[1] >= 0 && r[1] <= 3) { tp += r[1]; tpr++; }
      } else {
        elim = true;
        const isOpen = !meta[4] || meta[4] === "Open";
        if (isOpen) open = true;
        const dep = meta[3];
        if (dep != null && (bd === null || dep < bd)) { bd = dep; bcat = meta[4]; }
        if (dep === 0 && r[1] === 10) { won = true; bcat = meta[4]; }
        if (isOpen && dep != null && (obd === null || dep < obd)) obd = dep;
        if (isOpen && dep === 0 && r[1] === 10) owon = true;
      }
    }
    const pre = bcat && bcat !== "Open" ? bcat + " " : "";
    const lab = won ? pre + "Champion" : bd != null ? pre + (D.depthLabel[bd] || "") : elim ? "Broke" : "";
    const olab = owon ? "Champion" : obd != null ? (D.depthLabel[obd] || "") : "";
    return { lab, won, depth: bd, cat: bcat, open, owon, openFinal: obd === 0,
             odepth: obd, olab, pts: tp, prounds: tpr, avg: tns ? ts / tns : null };
  });
  return d.results;
}

function ratingOf(d) {
  const mu = d.amu ?? d.mu, sg = d.asg ?? d.sg;
  return mu == null ? { mu: null, sg: null, val: null } : { mu, sg, val: mu };
}

function peakOf(d) {
  const cur = ratingOf(d).val, pts = CV[d.i];
  if (!pts || !pts.length) return cur;
  let m = -Infinity;
  for (const p of pts) if (p[1] > m) m = p[1];
  return cur == null ? (m > -Infinity ? m : null) : Math.max(m, cur);
}

const COLS = [
  { k: "rank", t: "#",           w: "48px",  mw: "34px", tier: 1, num: 1,
    cell: (d, i) => `<div class="num mut">${i + 1}</div>` },
  { k: "name", t: "Debater",     w: "minmax(132px,1.3fr)", mw: "minmax(96px,1.4fr)", tier: 1,
    cell: d => `<div><a href="#">${esc(d.name)}</a></div>` },
  { k: "inst", t: "Institution", w: "minmax(90px,.9fr)", tier: 3,
    cell: d => `<div class="${d.inst ? "" : "mut"}">${esc(d.inst ?? "N/A")}</div>` },
  { k: "val",  t: "Rating",      w: "68px",  mw: "58px", tier: 1, num: 1,
    cell: d => `<div class="num" style="color:var(--blue);font-weight:bold">${k1(ratingOf(d).val)}</div>` },
  { k: "avg",  t: "Speaks",      w: "62px",  tier: 3, num: 1,
    cell: d => `<div class="num">${d.avg == null ? "—" : n1(d.avg)}</div>` },
  { k: "nt",   t: "Tournaments", w: "96px",  mw: "58px", tier: 2, num: 1,
    cell: d => `<div class="num">${d.nt}</div>` },
  { k: "obreaks", t: "Open breaks", w: "102px", tier: 3, num: 1,
    cell: d => `<div class="num">${pct(d.obreaks, d.nt)}</div>` },
  { k: "finals", t: "Finals",    w: "76px",  tier: 3, num: 1,
    cell: d => `<div class="num">${pct(d.finals, d.nt)}</div>` },
  { k: "wins", t: "Titles",      w: "76px",  mw: "52px", tier: 2, num: 1,
    cell: d => `<div class="num">${pct(d.wins, d.nt)}</div>` },
  { k: "last", t: "Last seen",   w: "minmax(120px,.7fr)", tier: 3, num: 1,
    cell: d => `<div class="num mut">${d.last == null ? "" : ago(d.last)}</div>` },
];
const tierNow = () => innerWidth >= 900 ? 3 : innerWidth >= 620 ? 2 : 1;
const activeCols = () => { const t = tierNow(); return COLS.filter(c => c.tier <= t); };
const gridOf = cols => cols.map(c => (tierNow() < 3 && c.mw) || c.w).join(" ");

function computeList() {
  listed = derived.filter(d => d.mu != null && d.nt >= F.minT &&
    (!F.from || (d.last != null && fmtDay(d.last) >= F.from)));
  const get = sortKey === "val" ? (d => ratingOf(d).val)
            : sortKey === "name" ? (d => d.lname)
            : sortKey === "inst" ? (d => d.inst?.toLowerCase() ?? null)
            : (d => d[sortKey]);
  listed.sort((a, b) => {
    const x = get(a), y = get(b);
    if (x === y) return a.lname < b.lname ? -1 : 1;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x < y ? -1 : 1) * sortDir;
  });
}

function renderBoard(m) {
  m.innerHTML = `<div class="wrap">
    <div class="filters">
      <div class="f"><label for="fT">Min tournaments</label>
        <input type="number" id="fT" min="1" max="99" step="1" style="width:64px" value="${F.minT}"></div>
      <div class="f"><label for="ff">Last seen on/after</label>
        <input type="text" id="ff" placeholder="YYYY-MM-DD" size="10" value="${esc(F.from)}"></div>
      <div class="f"><label for="fn">Find in list <span class="rv" id="lF"></span></label>
        <input type="text" id="fn" size="16" placeholder="jump to a debater"></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="freset">reset</button></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="fcsv">export CSV</button></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="fcount"></span></div>
    </div>
    <div class="tbl">
      <div class="thead" id="thead" style="grid-template-columns:${gridOf(activeCols())}">${activeCols().map(c =>
        `<div data-k="${c.k}" class="${c.k === sortKey ? "act" : ""}${c.num ? " num" : ""}">${c.t}${
          c.k === sortKey ? (sortDir < 0 ? " ▾" : " ▴") : ""}</div>`).join("")}</div>
      <div id="vport"><div id="spacer"><div id="rows"></div></div></div>
    </div>
  </div>`;
  $("fT").oninput = e => { F.minT = Math.max(1, +e.target.value || 1); update(); };
  $("ff").oninput = e => { F.from = e.target.value.trim(); update(); };
  $("fn").oninput = e => jumpTo(e.target.value.trim().toLowerCase());
  $("fn").onkeydown = e => { if (e.key === "Enter" && highlight >= 0) go("player", listed[highlight].i); };
  $("freset").onclick = () => { Object.assign(F, { minT: 7, from: "" }); highlight = -1; renderBoard(m); update(); };
  $("fcsv").onclick = exportCSV;
  m.onscroll = paint;
  update();
}
function update() {
  computeList();
  $("fcount").textContent = listed.length.toLocaleString() + " debaters";
  paint();
}
function listTop() {
  const m = $("main");
  return $("vport").getBoundingClientRect().top - m.getBoundingClientRect().top + m.scrollTop;
}
function paint() {
  const vp = $("vport");
  if (!vp) return;
  $("spacer").style.height = listed.length * ROWH + "px";
  const m = $("main");
  const top = m.scrollTop - listTop(), h = m.clientHeight;
  const a = Math.max(0, Math.floor(top / ROWH) - 8);
  const b = Math.max(0, Math.min(listed.length, Math.ceil((top + h) / ROWH) + 8));
  const cols = activeCols(), grid = gridOf(cols);
  let out = "";
  for (let i = a; i < b; i++) {
    const d = listed[i];
    out += `<div class="trow${i % 2 ? " odd" : ""}${i === highlight ? " hi" : ""}" data-i="${d.i}"
      style="grid-template-columns:${grid};position:absolute;top:${i * ROWH}px;left:0;right:0">${
      cols.map(c => c.cell(d, i)).join("")}</div>`;
  }
  $("rows").innerHTML = out;
}
function jumpTo(q) {
  const note = $("lF");
  if (!q) { highlight = -1; note.textContent = ""; return paint(); }
  const alt = d => aliasOf(d.i).some(a => a.toLowerCase().startsWith(q));
  let hit = listed.findIndex(d => d.lname.startsWith(q));
  if (hit < 0) hit = listed.findIndex(alt);
  if (hit < 0) hit = listed.findIndex(d => d.lname.includes(q));
  if (hit < 0) hit = listed.findIndex(d => aliasOf(d.i).some(a => a.toLowerCase().includes(q)));
  highlight = hit;
  note.textContent = hit < 0 ? "not in this list" : "#" + (hit + 1) + " of " + listed.length.toLocaleString();
  if (hit >= 0) {
    const m = $("main");
    m.scrollTop = Math.max(0, listTop() + hit * ROWH - m.clientHeight / 2 + ROWH / 2);
  }
  paint();
}
function exportCSV() {
  const rows = [["rank", "name", "institution", "rating", "speaks_last30", "tournaments",
                 "open_breaks", "open_break_rate", "finals", "final_rate",
                 "titles", "title_rate", "last_seen"].join(",")];
  const rate = (n, of) => of ? (n / of).toFixed(3) : "";
  listed.forEach((d, i) => rows.push([i + 1, `"${d.name.replace(/"/g, '""')}"`,
    `"${(d.inst ?? "").replace(/"/g, '""')}"`,
    Math.round(SCALE(ratingOf(d).val)),
    d.avg == null ? "" : d.avg.toFixed(2), d.nt,
    d.obreaks, rate(d.obreaks, d.nt), d.finals, rate(d.finals, d.nt),
    d.wins, rate(d.wins, d.nt),
    d.last == null ? "" : fmtDay(d.last)].join(",")));
  const url = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "debater_ranking.csv" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const SIZE_W = 150, WIN_BONUS = 100, ROUND_W = 60, ROUND_BASE = 5, ROUND_CAP = 10;

const roomsOf = t => t.field ? Math.ceil(t.field / 8) : 0;
const prelimsOf = t => t.rounds.filter(r => r[1] === "P").length;
function accolades(d, car) {
  const out = [];
  car.forEach((e, j) => {
    const res = resultsOf(d)[j];
    if (res.odepth == null) return;
    const t = T[e[0]], rounds = t.rounds;
    let best = null, ridx = -1;
    for (const r of e[3]) {
      const meta = rounds[r[0]];
      if (!meta || meta[1] === "P" || meta[3] == null) continue;
      if (meta[4] && meta[4] !== "Open") continue;
      if (best === null || meta[3] < best) { best = meta[3]; ridx = r[0]; }
    }
    const fs = ridx >= 0 ? rounds[ridx][6] : undefined;
    if (fs == null) return;
    const rooms = roomsOf(t);
    out.push({ ti: e[0], lab: res.olab,
               score: SCALE(fs) + SIZE_W * Math.log2(Math.max(rooms, 2))
                    + ROUND_W * (Math.min(prelimsOf(t) || ROUND_BASE, ROUND_CAP) - ROUND_BASE)
                    + (res.owon ? WIN_BONUS : 0) });
  });
  return out.sort((a, b) => b.score - a.score).slice(0, 3);
}

const AFF_MIN = 5, AFF_K = 8;
function tagAffinity(d) {
  const per = new Map();
  let n0 = 0, s0 = 0;
  for (const e of (C[d.i] || [])) {
    const rm = T[e[0]].rounds;
    for (const r of e[3]) {
      const meta = rm[r[0]];
      if (!meta || meta[1] !== "P" || r[1] < 0 || r[1] > 3 || r[4] == null) continue;
      const resid = r[1] - r[4];
      n0++;
      s0 += resid;
      const tags = new Set();
      for (const mo of (meta[5] || [])) for (const t of (mo.tg || [])) tags.add(t);
      for (const t of tags) {
        const a = per.get(t) || { n: 0, sum: 0, pts: 0 };
        a.n++;
        a.sum += resid;
        a.pts += r[1];
        per.set(t, a);
      }
    }
  }
  if (!n0) return null;
  const mean = s0 / n0;
  return { rows: [...per].filter(([, a]) => a.n >= AFF_MIN)
    .map(([t, a]) => ({ t, n: a.n, avg: a.pts / a.n, dev: a.sum / a.n - mean,
                        ds: (a.sum - a.n * mean) / (a.n + AFF_K) }))
    .sort((x, y) => y.ds - x.ds) };
}
function tagCard(d) {
  const a = tagAffinity(d);
  if (!a || !a.rows.length) return "";

  const diff = v => `<span style="color:${v >= 0 ? "#1a7f37" : "#b3261e"}">${
    (v >= 0 ? "+" : "") + v.toFixed(2)}</span>`;
  return `<div class="card"><h3>Motion types</h3>
    <div class="scrollx"><table class="d" style="width:auto"><thead><tr>
      <th></th>${a.rows.map(r => `<th class="n">${esc(cap(D.tags[r.t] || ""))}</th>`).join("")}
    </tr></thead><tbody>
      <tr><td class="mut" style="white-space:nowrap">vs expected</td>${
        a.rows.map(r => `<td class="n">${diff(r.dev)}</td>`).join("")}</tr>
      <tr><td class="mut" style="white-space:nowrap">Pts / round</td>${
        a.rows.map(r => `<td class="n">${r.avg.toFixed(2)}</td>`).join("")}</tr>
      <tr><td class="mut" style="white-space:nowrap">Rounds</td>${
        a.rows.map(r => `<td class="n mut">${r.n}</td>`).join("")}</tr>
    </tbody></table></div></div>`;
}
function renderPlayer(m, i) {
  const d = derived[i], car = C[i] || [], r = ratingOf(d);
  if (!car.length) {
    m.innerHTML = `<div class="wrap">${crumb("board", "debaters")}
      <div class="card"><p class="mut">This profile is not available.</p></div></div>`;
    return;
  }
  const sub = [];
  if (d.insts.length) sub.push(d.insts.map(x =>
    `<a href="#" data-in="${esc(x.key)}">${esc(x.name)}</a>`).join(" → "));
  for (const a of accolades(d, car))
    sub.push(`<a href="#" data-t="${a.ti}">${esc(T[a.ti].n)}</a> ${esc(a.lab)}`);
  const standing = e => e[4] ? `<span class="mut">${e[4]}/${e[5]}</span>` : "";
  const resultCell = (res, e) => res.won ? `<b class="win">${esc(res.lab)}</b>`
    : esc(res.lab) || standing(e);
  m.innerHTML = `<div class="wrap">
    ${crumb("board", "debaters")}
    <div class="phead"><h2>${esc(d.name)}</h2>${
      sub.length ? `<div class="sub">${sub.join(", ")}</div>` : ""}</div>
    <div class="scrollx" style="margin-bottom:14px"><table class="d" style="width:auto"><thead><tr>
      <th class="n">Rating</th><th class="n">Peak rating</th><th class="n">Speaks (last 30)</th>
      <th class="n">Pts / prelim</th><th class="n">Titles</th><th class="n">Finals</th><th class="n">Open breaks</th>
    </tr></thead><tbody><tr>
      <td class="n" style="color:var(--blue);font-weight:bold">${k1(r.val)}</td>
      <td class="n">${k1(peakOf(d))}</td>
      <td class="n">${n1(d.avg)}</td>
      <td class="n">${d.ppr == null ? "—" : d.ppr.toFixed(2)}</td>
      <td class="n">${d.wins}</td><td class="n">${d.finals}</td><td class="n">${d.obreaks}</td>
    </tr></tbody></table></div>
    ${CV[i] ? `<div style="margin-bottom:14px">${chart(CV[i])}</div>` : ""}
    <div class="card"><h3>Debating Career</h3>
      <table class="d"><thead><tr><th>Date</th><th>Tournament</th><th>Team</th>
        <th>With</th><th class="n">Pts</th><th class="n">Speaks</th><th>Result</th></tr></thead>
      <tbody>${car.map((e, j) => [e, j]).reverse().map(([e, j]) => {
        const t = T[e[0]], res = resultsOf(d)[j];
        return `<tr class="clk" data-x="${j}">
          <td class="n mut">${esc(t.d)}</td>
          <td><a href="#" data-t="${e[0]}">${esc(t.n)}</a></td>
          <td class="mut">${esc(e[1] || "—")}</td>
          <td>${e[2].map(p => `<a href="#" data-p="${p}">${esc(P[p][0])}</a>`).join(", ") || "—"}</td>
          <td class="n">${ptsOf(res.pts, res.prounds)}</td>
          <td class="n">${n1(res.avg)}</td>
          <td>${resultCell(res, e)}</td></tr>
          <tr id="x${j}" hidden><td colspan="8">${roundsHtml(e, t)}</td></tr>`;
      }).join("")}</tbody></table></div>
    <div class="card"><h3>Most frequent partners</h3>${partners(i)}</div>
    ${tagCard(d)}
    ${judgeOf.has(i) ? judgingCareer(judgeOf.get(i)) : ""}
  </div>`;
}
function roundsHtml(e, t) {
  return `<div class="rounds">${e[3].map(r => {
    const meta = t.rounds[r[0]];
    if (!meta) return "";
    const side = r[2] >= 0 ? " " + D.sides[r[2]].toUpperCase() : "";
    const res = meta[1] === "P" && r[1] >= 0 && r[1] <= 3 ? ` <b class="p${r[1]}">${["4th","3rd","2nd","1st"][r[1]]}</b>`
      : r[1] === 10 ? ' <b class="p3">adv</b>' : r[1] === 11 ? ' <b class="p0">out</b>' : "";
    return `${esc(meta[2])}${side}${res}${r[3] != null ? " " + n1(r[3]) : ""}`;
  }).filter(Boolean).join(" &nbsp;|&nbsp; ")}</div>`;
}
function partners(i) {
  const cnt = new Map();
  for (const e of C[i] || []) for (const p of e[2]) cnt.set(p, (cnt.get(p) || 0) + e[3].length);
  const top = [...cnt].sort((a, b) => b[1] - a[1]).slice(0, 14);
  return top.map(([p, c]) => `<a href="#" data-p="${p}">${esc(P[p][0])}</a> (${c})`)
    .join(", ") || '<span class="mut">—</span>';
}

function chart(pts) {
  const W = 880, H = 240, L = 56, R = 16, TP = 14, B = 28;
  const q = pts.map(p => [p[0], SCALE(p[1])]);
  let lo = Math.min(...q.map(p => p[1])), hi = Math.max(...q.map(p => p[1]));
  const pad = (hi - lo) * 0.1 || 100;
  lo -= pad; hi += pad;
  const x0 = q[0][0], x1 = q[q.length - 1][0] || x0 + 1;
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R);
  const Y = v => TP + (hi - v) / (hi - lo) * (H - TP - B);
  const step = niceStep(hi - lo);
  let ax = "";
  for (let v = Math.ceil(lo / step) * step; v < hi; v += step)
    ax += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/>
           <line class="axl" x1="${L - 4}" x2="${L}" y1="${Y(v)}" y2="${Y(v)}"/>
           <text class="ax" x="${L - 7}" y="${Y(v) + 3}" text-anchor="end">${Math.round(v)}</text>`;
  for (let y = new Date(EPOCH + x0 * DAY).getUTCFullYear() + 1; ; y++) {
    const dv = Math.round((Date.UTC(y, 0, 1) - EPOCH) / DAY);
    if (dv > x1 + 30) break;
    ax += `<line class="grid" x1="${X(dv)}" x2="${X(dv)}" y1="${TP}" y2="${H - B}"/>
           <line class="axl" x1="${X(dv)}" x2="${X(dv)}" y1="${H - B}" y2="${H - B + 4}"/>
           <text class="ax" x="${X(dv)}" y="${H - 9}" text-anchor="middle">${y}</text>`;
  }
  const line = q.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const marks = q.map(p => {
    const x = +X(p[0]).toFixed(1), y = +Y(p[1]).toFixed(1);
    return `<rect x="${x - 2.4}" y="${y - 2.4}" width="4.8" height="4.8"
      transform="rotate(45 ${x} ${y})" fill="#000080">
      <title>${fmtDay(p[0])}  rating ${Math.round(p[1])}</title></rect>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img"
    aria-label="rating over time">
    <rect x="${L}" y="${TP}" width="${W - L - R}" height="${H - TP - B}"
      fill="#ececec" stroke="#888" shape-rendering="crispEdges"/>
    ${ax}<path d="${line}" fill="none" stroke="#000080" stroke-width="1.5"/>${marks}</svg>`;
}
function niceStep(range) {
  const raw = range / 5, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p;
}

function qualifies(tid) {
  if (instMinBS == null) return true;
  const bs = (T[tid].bs || {}).Open;
  return bs != null && SCALE(bs) >= instMinBS;
}
function buildInsts() {

  if (instCache.key === instMinBS && instCache.rest === restReady) return instCache.rows;
  const acc = new Map();
  for (const d of derived) {
    for (const it of d.insts) {
      let a = acc.get(it.key);
      if (!a) acc.set(it.key, a = { name: it.name, key: it.key, people: new Set(),
                                    tourns: new Set(), win: new Set(), fin: new Set(),
                                    opn: new Set() });
      a.people.add(d.i);
    }

    (C[d.i] || []).forEach((e, j) => {
      const a = acc.get(d.instAt[j]);
      if (!a) return;
      const res = resultsOf(d)[j], team = JSON.stringify([e[0], e[1] || ""]);
      a.tourns.add(e[0]);
      if (!qualifies(e[0])) return;
      if (res.owon) a.win.add(team);
      if (res.openFinal) a.fin.add(team);
      if (res.open) a.opn.add(team);
    });
  }
  instCache = { key: instMinBS, rest: restReady, rows: [...acc.values()]
    .filter(a => a.people.size)
    .map(a => ({ name: a.name, key: a.key, people: a.people.size, tourns: a.tourns.size,
                 wins: a.win.size, finals: a.fin.size, obreaks: a.opn.size })) };
  return instCache.rows;
}

function countsFor(d, key) {
  let nt = 0, obreaks = 0, finals = 0, wins = 0;
  (C[d.i] || []).forEach((e, j) => {
    if (key && d.instAt[j] !== key) return;
    const res = resultsOf(d)[j];
    nt++;
    if (!qualifies(e[0])) return;
    if (res.open) obreaks++;
    if (res.openFinal) finals++;
    if (res.owon) wins++;
  });
  return { nt, obreaks, finals, wins };
}
const ICOLS = [
  { k: "name", t: "Institution" }, { k: "people", t: "Debaters", n: 1 },
  { k: "tourns", t: "Tournaments", n: 1 }, { k: "obreaks", t: "Open breaks", n: 1 },
  { k: "finals", t: "Finals", n: 1 }, { k: "wins", t: "Titles", n: 1 },
];
function fillInsts() {
  const rows = buildInsts().filter(x => !instQ || x.key.includes(instQ) ||
    akaFor(x.key).some(a => a.includes(instQ)));
  const get = instSort.k === "name" ? (x => x.key) : (x => x[instSort.k]);

  rows.sort((a, b) => {
    const x = get(a), y = get(b);
    if (x !== y) return (x < y ? -1 : 1) * instSort.dir;
    return (b.wins - a.wins) || (b.finals - a.finals) || (b.obreaks - a.obreaks)
        || (a.key < b.key ? -1 : 1);
  });
  $("icount").textContent = rows.length + " institutions";
  $("itbl").innerHTML = `<table class="d"><thead><tr>${ICOLS.map(c =>
    `<th data-is="${c.k}"${c.n ? ' class="n"' : ""}>${c.t}${
      c.k === instSort.k ? (instSort.dir < 0 ? " \u25be" : " \u25b4") : ""}</th>`).join("")}
  </tr></thead><tbody>${rows.map(x => `<tr class="clk" data-in="${esc(x.key)}">
    <td class="lnk">${esc(x.name)}</td><td class="n">${x.people}</td><td class="n mut">${x.tourns}</td>
    <td class="n">${x.obreaks || ""}</td><td class="n">${x.finals || ""}</td>
    <td class="n">${x.wins || ""}</td></tr>`).join("")}
  </tbody></table>`;
}
function renderInsts(m) {
  m.innerHTML = `<div class="wrap">
    <div class="filters">
      <div class="f"><label for="iq">Search institution</label>
        <input type="text" id="iq" size="22" value="${esc(instQ)}"></div>
      <div class="f"><label for="ibs">Min tournament break strength</label>
        <input type="number" id="ibs" step="50" style="width:90px" value="${instMinBS ?? ""}"></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="ireset">reset</button></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="icount"></span></div>
    </div>
    <div class="tbl" id="itbl"></div>
  </div>`;
  $("iq").oninput = () => { instQ = $("iq").value.trim().toLowerCase(); fillInsts(); };
  $("ibs").oninput = () => {
    const v = $("ibs").value.trim();
    instMinBS = v === "" ? null : +v;
    fillInsts();
  };
  $("ireset").onclick = () => {
    instQ = ""; instMinBS = INST_BS_DEFAULT;
    $("iq").value = ""; $("ibs").value = INST_BS_DEFAULT;
    fillInsts();
  };
  fillInsts();
}

function fillInstPeople(key) {
  const rows = derived.filter(d => d.insts.some(x => x.key === key))
    .map(d => ({ d, c: countsFor(d, key) }))
    .filter(x => x.c.nt >= instMinT)
    .filter(x => !instFrom || (x.d.last != null && fmtDay(x.d.last) >= instFrom))
    .sort((p, q) => (ratingOf(q.d).val ?? -99) - (ratingOf(p.d).val ?? -99));
  $("ipcount").textContent = rows.length + (rows.length === 1 ? " debater" : " debaters");
  $("instppl").innerHTML = `<table class="d"><thead><tr>
      <th>#</th><th>Debater</th><th class="n">Rating</th><th class="n">Tournaments</th>
      <th class="n">Open breaks</th><th class="n">Finals</th><th class="n">Titles</th>
      <th class="n">Last seen</th></tr></thead><tbody>
      ${rows.map(({ d, c }, i) => `<tr>
        <td class="n mut">${i + 1}</td>
        <td><a href="#" data-p="${d.i}">${esc(d.name)}</a></td>
        <td class="n" style="color:var(--blue)">${k1(ratingOf(d).val)}</td>
        <td class="n">${c.nt}</td><td class="n">${c.obreaks || ""}</td>
        <td class="n">${c.finals || ""}</td><td class="n">${c.wins || ""}</td>
        <td class="n mut">${d.last == null ? "" : ago(d.last)}</td></tr>`).join("")}
    </tbody></table>`;
}
function renderInst(m, key) {
  const a = buildInsts().find(x => x.key === key);
  if (!a) return renderInsts(m);
  m.innerHTML = `<div class="wrap">
    ${crumb("insts", "institutions")}
    <div class="phead"><h2>${esc(a.name)}</h2>
      ${(() => { const aka = akaFor(a.key);
        return aka.length ? `<div class="sub">a.k.a. ${aka.map(esc).join(", ")}</div>` : ""; })()}</div>
    <div class="filters">
      <div class="f"><label for="ipT">Min tournaments</label>
        <input type="number" id="ipT" min="1" max="99" step="1" style="width:64px" value="${instMinT}"></div>
      <div class="f"><label for="ipf">Last seen on/after</label>
        <input type="text" id="ipf" placeholder="YYYY-MM-DD" size="10" value="${esc(instFrom)}"></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="ipreset">reset</button></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="ipcount"></span></div>
    </div>
    <div class="card"><h3>Debaters</h3><div id="instppl"></div></div>
  </div>`;
  $("ipT").oninput = () => { instMinT = Math.max(1, +$("ipT").value || 1); fillInstPeople(key); };
  $("ipf").oninput = () => { instFrom = $("ipf").value.trim(); fillInstPeople(key); };
  $("ipreset").onclick = () => {
    instMinT = INST_MINT_DEFAULT; instFrom = "";
    $("ipT").value = instMinT; $("ipf").value = "";
    fillInstPeople(key);
  };
  fillInstPeople(key);
}

function qualifiesJ(tid) {
  if (judgeMinBS == null) return true;
  const bs = (T[tid].bs || {}).Open;
  return bs != null && SCALE(bs) >= judgeMinBS;
}
const ROOM_WINDOW = 15;
function judgeStats(j) {
  const rows = (JC[j] || []).filter(r => qualifiesJ(r[0]));
  let elim = 0, finals = 0, chaired = 0;
  for (const r of rows) {
    const meta = T[r[0]].rounds[r[1]];
    if (meta && meta[1] === "E") {
      elim++;
      if (meta[3] === 0) finals++;
    }
    if (r[2] === 1) chaired++;
  }
  const recent = rows.slice(-ROOM_WINDOW).map(r => r[3]).filter(v => v != null);
  return { j, name: JN[j], rounds: rows.length, elim, finals, chaired,
           room: recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null,
           last: rows.length ? T[rows[rows.length - 1][0]].d : null };
}
function buildJudges() {
  if (judgeCache.key === judgeMinBS && judgeCache.rest === restReady) return judgeCache.rows;
  judgeCache = { key: judgeMinBS, rest: restReady,
                 rows: JN.map((_n, j) => judgeStats(j)).filter(x => x.rounds) };
  return judgeCache.rows;
}
const JCOLS = [
  { k: "name", t: "Judge" }, { k: "rounds", t: "Rounds", n: 1 },
  { k: "elim", t: "Outrounds", n: 1 }, { k: "finals", t: "Finals", n: 1 },
  { k: "chaired", t: "Chaired", n: 1 },
  { k: "room", t: "Room strength", n: 1 },
];
const judgeHref = j => JLINK[j] >= 0 ? `data-p="${JLINK[j]}"` : `data-j="${j}"`;
function fillJudges() {
  const q = judgeQ;
  const rows = buildJudges().filter(x => !q || x.name.toLowerCase().includes(q));
  const get = judgeSort.k === "name" ? (x => x.name.toLowerCase()) : (x => x[judgeSort.k]);
  rows.sort((a, b) => {
    const x = get(a) ?? -Infinity, y = get(b) ?? -Infinity;
    if (x !== y) return (x < y ? -1 : 1) * judgeSort.dir;
    return (b.rounds - a.rounds) || (a.name < b.name ? -1 : 1);
  });
  $("jcount").textContent = rows.length + (rows.length === 1 ? " judge" : " judges");
  const arrow = k => k === judgeSort.k ? (judgeSort.dir < 0 ? " \u25be" : " \u25b4") : "";
  $("jtbl").innerHTML = `<table class="d"><thead><tr>${JCOLS.map(c =>
    `<th data-js="${c.k}"${c.n ? ' class="n"' : ""}>${c.t}${arrow(c.k)}</th>`).join("")}
    <th class="n">Last seen</th>
  </tr></thead><tbody>${rows.slice(0, 500).map(x => `<tr>
    <td><a href="#" ${judgeHref(x.j)}>${esc(x.name)}</a></td>
    <td class="n">${x.rounds}</td><td class="n">${x.elim || ""}</td>
    <td class="n">${x.finals || ""}</td><td class="n">${x.chaired || ""}</td>
    <td class="n" style="color:var(--blue)">${k1(x.room)}</td>
    <td class="n mut">${x.last || ""}</td></tr>`).join("")}
  </tbody></table>${rows.length > 500
    ? `<div class="mut" style="padding:8px">showing the top 500 of ${rows.length}</div>` : ""}`;
}
function renderJudges(m) {
  m.innerHTML = `<div class="wrap">
    <div class="filters">
      <div class="f"><label for="jq">Search</label>
        <input type="text" id="jq" size="22" value="${esc(judgeQ)}"></div>
      <div class="f"><label for="jbs">Min tournament break strength</label>
        <input type="number" id="jbs" step="50" style="width:90px" value="${judgeMinBS ?? ""}"></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="jreset">reset</button></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="jcount"></span></div>
    </div>
    <div class="tbl" id="jtbl"></div>
  </div>`;
  $("jq").oninput = () => { judgeQ = $("jq").value.trim().toLowerCase(); fillJudges(); };
  $("jbs").oninput = () => {
    const v = $("jbs").value.trim();
    judgeMinBS = v === "" ? null : +v;
    fillJudges();
  };
  $("jreset").onclick = () => {
    judgeQ = ""; judgeMinBS = JUDGE_BS_DEFAULT;
    $("jq").value = ""; $("jbs").value = JUDGE_BS_DEFAULT;
    fillJudges();
  };
  fillJudges();
}
const ROLE_LABEL = ["Panellist", "Chair", "Trainee"];

function judgingCareer(j) {
  const st = judgeStats(j), rows = (JC[j] || []).filter(r => qualifiesJ(r[0]));
  if (!rows.length) return "";
  return `<div class="card"><h3>Judging Career</h3>
    <div class="scrollx" style="margin-bottom:12px"><table class="d" style="width:auto"><thead><tr>
      <th class="n">Rounds</th><th class="n">Outrounds</th><th class="n">Finals</th>
      <th class="n">Chaired</th><th class="n">Room strength (last ${ROOM_WINDOW})</th>
    </tr></thead><tbody><tr>
      <td class="n">${st.rounds}</td><td class="n">${st.elim}</td>
      <td class="n">${st.finals}</td><td class="n">${st.chaired}</td>
      <td class="n" style="color:var(--blue);font-weight:bold">${k1(st.room)}</td>
    </tr></tbody></table></div>
    <table class="d"><thead><tr><th>Date</th><th>Tournament</th><th class="n">Rounds</th>
      <th class="n">Outrounds</th><th class="n">Finals</th><th class="n">Chaired</th>
      <th class="n">Room strength</th></tr></thead>
    <tbody>${byTournament(rows).map((g, n) => `
      <tr class="clk" data-jx="${n}">
        <td class="n mut">${esc(T[g.tid].d)}</td>
        <td><a href="#" data-t="${g.tid}">${esc(T[g.tid].n)}</a></td>
        <td class="n">${g.rows.length}</td><td class="n">${g.elim || ""}</td>
        <td class="n">${g.finals || ""}</td><td class="n">${g.chaired || ""}</td>
        <td class="n">${k1(g.room)}</td></tr>
      <tr id="jx${n}" hidden><td colspan="7">${judgedRounds(g)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function byTournament(rows) {
  const out = [];
  for (const r of rows) {
    let g = out.length && out[out.length - 1].tid === r[0] ? out[out.length - 1] : null;
    if (!g) out.push(g = { tid: r[0], rows: [], elim: 0, finals: 0, chaired: 0, room: null });
    g.rows.push(r);
    const meta = T[r[0]].rounds[r[1]];
    if (meta && meta[1] === "E") { g.elim++; if (meta[3] === 0) g.finals++; }
    if (r[2] === 1) g.chaired++;
  }
  for (const g of out) {
    const v = g.rows.map(r => r[3]).filter(x => x != null);
    g.room = v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  }
  return out.reverse();
}
function judgedRounds(g) {
  return `<div class="rounds">${g.rows.map(r => {
    const meta = T[g.tid].rounds[r[1]];
    if (!meta) return "";
    return `${esc(meta[2])} <b>${ROLE_LABEL[r[2]] || ""}</b>${r[3] == null ? "" : " " + k1(r[3])}`;
  }).filter(Boolean).join(" &nbsp;|&nbsp; ")}</div>`;
}
function renderJudge(m, j) {
  const st = judgeStats(j);
  m.innerHTML = `<div class="wrap">
    ${crumb("judges", "judges")}
    <div class="phead"><h2>${esc(JN[j])}</h2>
      <div class="sub">Judge</div></div>
    ${judgingCareer(j) || `<div class="card"><p class="mut">No rounds at tournaments
      clearing the current break-strength filter.</p></div>`}
  </div>`;
}

const balStd = a => {
  const m = (a[0] + a[1] + a[2] + a[3]) / 4;
  return -Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / 4);
};

const centre = a => { const m = (a[0] + a[1] + a[2] + a[3]) / 4; return a.map(v => v - m); };
const benches = ([og, oo, cg, co]) =>
  ({ gov: (og + cg) / 2, opp: (oo + co) / 2, opening: (og + oo) / 2, closing: (cg + co) / 2 });

function mergeByMotion(occs) {
  const groups = new Map();
  for (const o of occs) {
    let g = groups.get(o.mi);
    if (!g) groups.set(o.mi, g = { mi: o.mi, text: o.text, info: "", tags: o.tags,
                                   occ: [], wsum: 0, sum: [0, 0, 0, 0], adj: o.adj });
    if (o.adj != null && (g.adj == null || o.adj < g.adj)) g.adj = o.adj;
    g.info = g.info || o.info;
    g.occ.push({ ti: o.ti, rname: o.rname, rooms: o.rooms });
    g.wsum += o.rooms;
    for (let i = 0; i < 4; i++) g.sum[i] += o.sides[i] * o.rooms;
  }
  return [...groups.values()].map(g => {
    const sides = g.sum.map(s => s / (g.wsum || 1));
    return { mi: g.mi, occ: g.occ, text: g.text, info: g.info, tags: g.tags, rooms: g.wsum,
             sides, ...benches(sides), bal: balStd(sides), adj: g.adj };
  });
}
const occOf = b => ({ ti: b[0], rname: b[1], text: b[2], info: b[3], tags: b[4],
                      rooms: b[5], sides: centre(b.slice(7, 11)), adj: b[11], mi: b[12] });
function buildBalance() {
  const minBSv = balMinBS == null ? null : balMinBS;
  return mergeByMotion((D.balance || [])
    .filter(b => b[5] >= balMinRooms)
    .filter(b => minBSv == null || (b[6] != null && SCALE(b[6]) >= minBSv))
    .filter(b => b[4].some(t => balTags.has(t)))
    .filter(b => !balMoQ || b[2].toLowerCase().includes(balMoQ))
    .filter(b => !balTourQ || T[b[0]].n.toLowerCase().includes(balTourQ))
    .map(occOf));
}

let balAllCache = null;
function balAll() {
  if (!balAllCache) {
    balAllCache = new Map();
    for (const g of mergeByMotion((D.balance || []).map(occOf))) balAllCache.set(g.mi, g);
  }
  return balAllCache;
}
function avgRow(rows) {
  const n = rows.length || 1;
  const sides = [0, 1, 2, 3].map(i => rows.reduce((s, r) => s + r.sides[i], 0) / n);
  const av = rows.filter(r => r.adj != null);
  return { sides, ...benches(sides), bal: rows.reduce((s, r) => s + r.bal, 0) / n,
           adj: av.length ? av.reduce((s, r) => s + r.adj, 0) / av.length : null };
}
const BAL_COLS = [
  { k: "og", t: "OG", get: x => x.sides[0] }, { k: "oo", t: "OO", get: x => x.sides[1] },
  { k: "cg", t: "CG", get: x => x.sides[2] }, { k: "co", t: "CO", get: x => x.sides[3] },
  { k: "gov", t: "Gov", get: x => x.gov }, { k: "opp", t: "Opp", get: x => x.opp },
  { k: "opening", t: "Opn", get: x => x.opening }, { k: "closing", t: "Cls", get: x => x.closing },
  { k: "bal", t: "Balance", get: x => x.bal },

  { k: "adj", t: "Evidence", get: x => x.adj == null ? null : -x.adj },
];
let balRows = [], balInfoOpen = -1;
function toggleBalInfo(i) { balInfoOpen = balInfoOpen === i ? -1 : i; fillBalance(); }

function similarPanel(r) {
  const lst = (D.nbr || [])[r.mi] || [];
  if (!lst.length) return "";
  const all = balAll();
  const fv = v => `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
  const body = lst.map(([mi]) => {
    const g = all.get(mi);
    if (!g) return "";
    const o = g.occ[0];
    return `<tr>
      <td>${esc(g.text)}<div class="sub mut">${
        o ? `<a href="#" data-t="${o.ti}">${esc(T[o.ti].n)}</a> &middot; ${esc(o.rname)}` : ""}${
        g.occ.length > 1 ? ` &middot; +${g.occ.length - 1} more` : ""}</div></td>
      <td class="n">${fv(g.gov)}</td><td class="n">${fv(g.opp)}</td>
      <td class="n">${fv(g.bal)}</td></tr>`;
  }).join("");
  return `<div class="simwrap"><b>Similar motions</b>
    <table class="d sim"><thead><tr>
      <th>Motion</th><th class="n">Gov</th><th class="n">Opp</th>
      <th class="n">Balance</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
function fillBalance() {
  const rows = buildBalance();
  const col = BAL_COLS.find(c => c.k === balSort.k);
  const sortGet = balSort.k === "mo" ? (x => x.text.toLowerCase()) : (col ? col.get : (x => x.bal));
  rows.sort((a, b) => { const x = sortGet(a), y = sortGet(b);
    return x === y ? 0 : (x < y ? -1 : 1) * balSort.dir; });
  balRows = rows;
  const avg = avgRow(rows);
  $("balcount").textContent = rows.length + " motions";
  const arrow = k => k === balSort.k ? (balSort.dir < 0 ? " ▾" : " ▴") : "";
  const fv = v => v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
  const ncol = BAL_COLS.length + 1;
  $("baltbl").innerHTML = `<table class="d"><thead><tr>
    <th data-bs="mo">Motion${arrow("mo")}</th>${
    BAL_COLS.map(c => `<th class="n" data-bs="${c.k}">${c.t}${arrow(c.k)}</th>`).join("")}
  </tr></thead><tbody>
    <tr class="avgrow"><td class="mut">Average (${rows.length} motions)</td>${
      BAL_COLS.map(c => `<td class="n">${fv(c.get(avg))}</td>`).join("")}</tr>
    ${rows.map((r, i) => `<tr>
    <td><a href="#" data-bi="${i}">${esc(r.text)}</a>
      <div class="sub mut">${r.occ.map(o =>
        `<a href="#" data-t="${o.ti}">${esc(T[o.ti].n)}</a> &middot; ${esc(o.rname)}`).join(", ")}</div></td>${
      BAL_COLS.map(c => `<td class="n">${fv(c.get(r))}</td>`).join("")}</tr>${
      i === balInfoOpen ? `<tr><td colspan="${ncol}">
        <div class="motion">${r.info ? `<b>Infoslide</b><div class="info">${esc(r.info)}</div>` : ""}${
        similarPanel(r)}</div></td></tr>` : ""}`).join("")}
  </tbody></table>`;
}
function renderBalance(m) {
  if (!balTags) balTags = new Set(D.tags.map((_, i) => i));
  const allOn = balTags.size === D.tags.length;
  m.innerHTML = `<div class="wrap">
    <div class="filters">
      <div class="f"><label for="bmq">Search motion</label>
        <input type="text" id="bmq" size="20" value="${esc(balMoQ)}"></div>
      <div class="f"><label for="btq">Search tournament</label>
        <input type="text" id="btq" size="20" value="${esc(balTourQ)}"></div>
      <div class="f"><label for="brm">Min rooms</label>
        <input type="number" id="brm" min="1" step="1" style="width:64px" value="${balMinRooms}"></div>
      <div class="f"><label for="bbs">Min tournament break strength</label>
        <input type="number" id="bbs" step="50" style="width:90px" value="${balMinBS ?? ""}"></div>
      <div class="f"><label>&nbsp;</label><button class="btn" id="breset">reset</button></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="balcount"></span></div>
    </div>
    <div class="tagf"><div class="tagrow"><label style="font-weight:bold">
        <input type="checkbox" id="btall"${allOn ? " checked" : ""}> All</label> ${
      D.tags.map((t, i) =>
      `<label><input type="checkbox" data-bt="${i}"${balTags.has(i) ? " checked" : ""}> ${esc(cap(t))}</label>`
      ).join(" ")}</div><div class="tagrow"></div></div>
    <div class="tbl" id="baltbl"></div>
  </div>`;
  watchTagf();
  $("bmq").oninput = () => { balMoQ = $("bmq").value.trim().toLowerCase(); fillBalance(); };
  $("btq").oninput = () => { balTourQ = $("btq").value.trim().toLowerCase(); fillBalance(); };
  $("brm").oninput = () => { balMinRooms = +$("brm").value || 0; fillBalance(); };
  $("bbs").oninput = () => {
    const v = $("bbs").value.trim();
    balMinBS = v === "" ? null : +v;
    fillBalance();
  };
  $("breset").onclick = () => {
    balMinRooms = 15; balMinBS = INST_BS_DEFAULT; balTags = new Set(D.tags.map((_, i) => i));
    balMoQ = ""; balTourQ = "";
    renderBalance(m);
  };
  const syncAll = () => { $("btall").checked = balTags.size === D.tags.length; };
  $("btall").onchange = () => {
    balTags = $("btall").checked ? new Set(D.tags.map((_, i) => i)) : new Set();
    document.querySelectorAll("[data-bt]").forEach(cb => cb.checked = $("btall").checked);
    fillBalance();
  };
  document.querySelectorAll("[data-bt]").forEach(cb => cb.onchange = () => {
    const i = +cb.dataset.bt;
    if (cb.checked) balTags.add(i); else balTags.delete(i);
    syncAll();
    fillBalance();
  });
  fillBalance();
}

function championOf(ti) {
  if (!champs || champsRest !== restReady) {
    champs = new Map();
    champsRest = restReady;
    derived.forEach(d => (C[d.i] || []).forEach((e, j) => {
      const res = resultsOf(d)[j];
      if (res.won && (!res.cat || res.cat === "Open"))
        (champs.get(e[0]) ?? champs.set(e[0], []).get(e[0])).push(d.i);
    }));
  }
  const w = champs.get(ti);
  return w ? w.slice(0, 3).map(i => `<a href="#" data-p="${i}">${esc(P[i][0])}</a>`).join(" &amp; ")
           : '<span class="mut">—</span>';
}
function fillTours() {
  const rows = T.map((t, i) => ({ i, n: t.n, d: t.d, bs: (t.bs || {}).Open ?? null,
                                  bn: (t.bn || {}).Open ?? null,
                                  f: roomsOf(t),
                                  r: t.rounds.filter(x => x[1] === "P").length }))
    .filter(x => !tourQ || x.n.toLowerCase().includes(tourQ));
  const get = tourSort.k === "n" ? (x => x.n.toLowerCase()) : (x => x[tourSort.k]);
  rows.sort((a, b) => { const x = get(a), y = get(b);
    return x === y ? 0 : (x < y ? -1 : 1) * tourSort.dir; });
  $("tcount").textContent = rows.length + " tournaments";
  $("ttbl").innerHTML = `<table class="d"><thead><tr>
    <th data-ts="d">Date</th><th data-ts="n">Tournament</th>
    <th class="n" data-ts="r">Rounds</th><th class="n" data-ts="f">Rooms</th>
    <th class="n" data-ts="bn">Break size</th>
    <th class="n" data-ts="bs">Break strength</th><th>Champion</th>
  </tr></thead><tbody>${rows.map(x => `<tr class="clk" data-tr="${x.i}">
    <td class="n mut">${esc(x.d)}</td><td class="lnk">${esc(x.n)}</td>
    <td class="n mut">${x.r}</td><td class="n">${x.f}</td>
    <td class="n">${x.bn ?? "—"}</td>
    <td class="n">${k1(x.bs)}</td><td>${championOf(x.i)}</td></tr>`).join("")}
  </tbody></table>`;
}
function renderTours(m) {
  m.innerHTML = `<div class="wrap">
    <div class="filters">
      <div class="f"><label for="tq">Search tournament</label>
        <input type="text" id="tq" size="26" value="${esc(tourQ)}"></div>
      <div class="f"><label>&nbsp;</label><span class="count" id="tcount"></span></div>
    </div>
    <div class="tbl" id="ttbl"></div>
  </div>`;
  $("tq").oninput = () => { tourQ = $("tq").value.trim().toLowerCase(); fillTours(); };
  fillTours();
  if (!restReady) loadRest().then(() => { if (view.v === "tours") fillTours(); });
}

function breakStrength(t) {
  const size = (t.bn || {}).Open, str = (t.bs || {}).Open;
  if (size == null && str == null) return "";
  return `<div class="card"><h3>Open break</h3>
    <div class="scrollx"><table class="d" style="width:auto"><thead><tr>
      <th class="n">Teams</th><th class="n">Break strength</th>
    </tr></thead><tbody><tr>
      <td class="n">${size ?? "—"}</td><td class="n">${k1(str)}</td>
    </tr></tbody></table></div></div>`;
}

let motionOpen = -1;
function showMotion(i) {
  const box = $("motion");
  if (!box) return;
  if (motionOpen === i) { motionOpen = -1; box.innerHTML = ""; return; }
  motionOpen = i;
  const r = T[view.id].rounds[i];
  box.innerHTML = `<div class="motion"><b>${esc(r[2])}</b>${r[5].map(mo =>
    `<div class="mt">${esc(mo.t)}</div>${mo.i ? `<div class="info">${esc(mo.i)}</div>` : ""}${tagChips(mo)}`
    ).join("")}</div>`;
}
const tagChips = mo => mo.tg && mo.tg.length
  ? `<div class="tags">${mo.tg.map(ix => esc(cap(D.tags[ix]))).join(", ")}</div>` : "";

let xmotionOpen = -1;
function showExtraMotion(i) {
  const box = $("xmotion");
  if (!box) return;
  if (xmotionOpen === i) { xmotionOpen = -1; box.innerHTML = ""; return; }
  xmotionOpen = i;
  const x = T[view.id].xm[i];
  box.innerHTML = `<div class="motion"><b>${esc(x.label)}</b>
    <div class="mt">${esc(x.t)}</div>
    <div class="info">${x.src === "wikipedia" ? "from Wikipedia" : "recovered from the filmed round"}</div>${tagChips(x)}</div>`;
}
function renderTour(m, ti) {
  const t = T[ti];
  const field = [];
  derived.forEach(d => (C[d.i] || []).forEach((e, j) => {
    if (e[0] !== ti) return;
    const res = resultsOf(d)[j];
    field.push({ i: d.i, name: d.name, team: e[1],
                 pts: res.prounds ? res.pts : null, prounds: res.prounds,
                 avg: res.avg, lab: res.lab, won: res.won, rank: e[4], of: e[5],
                 depth: res.depth ?? 99, rate: ratingOf(d).val });
  }));
  field.sort((a, b) => ((b.avg ?? -1) - (a.avg ?? -1)) ||
    (a.depth - b.depth) || (b.won - a.won) || ((b.pts ?? -1) - (a.pts ?? -1)));
  m.innerHTML = `<div class="wrap">
    ${crumb("tours", "tournaments")}
    <div class="phead"><div>
      <h2>${esc(t.n)}</h2>
      <div class="sub">${esc(t.d)} &middot; ${roomsOf(t)} rooms &middot;
        ${t.rounds.filter(r => r[1] === "P").length} prelim rounds${t.u ? ` &middot; <a href="${esc(t.u)}" target="_blank" rel="noopener">tab site ↗</a>` : ""}${
        t.partial ? " &middot; <i>partially reconstructed</i>" : ""}</div>
    </div></div>
    ${breakStrength(t)}
    <div class="card"><h3>Rounds</h3><div class="rounds">${t.rounds.map((r, i) =>
      r[5] && r[5].length ? `<a href="#" data-rd="${i}">${esc(r[2])}</a>` : esc(r[2])
      ).join(" &nbsp;|&nbsp; ")}</div><div id="motion"></div>${
      (t.xm && t.xm.length) ? `<div class="rounds" style="margin-top:6px">${
        t.xm.map((x, i) => `<a href="#" data-xm="${i}">${esc(x.label)}</a>`).join(" &nbsp;|&nbsp; ")
      }</div><div id="xmotion"></div>` : ""}</div>
    <div class="card"><h3>Debaters</h3><table class="d"><thead><tr>
      <th>#</th><th>Debater</th><th>Team</th><th class="n">Pts</th><th class="n">Speaks</th>
      <th>Result</th><th class="n">Rating</th></tr></thead><tbody>
      ${field.map((f, k) => `<tr>
        <td class="n mut">${k + 1}</td>
        <td><a href="#" data-p="${f.i}">${esc(f.name)}</a></td>
        <td class="mut">${esc(f.team || "—")}</td>
        <td class="n">${ptsOf(f.pts, f.prounds)}</td><td class="n">${n1(f.avg)}</td>
        <td>${f.won ? `<b class="win">${esc(f.lab)}</b>`
             : esc(f.lab) || (f.rank ? `<span class="mut">${f.rank}/${f.of}</span>` : "")}</td>
        <td class="n" style="color:var(--blue)">${k1(f.rate)}</td></tr>`).join("")}
    </tbody></table></div>
  </div>`;
}

const REST_URL = "/rest.bin";
let restPromise = null, restReady = false;
function loadRest() {
  if (!restPromise) {
    restPromise = fetch(REST_URL).then(r => r.json()).then(r => {
      if (r.n_players != null && r.n_players !== P.length) {
        return fetch(REST_URL + "?v=" + encodeURIComponent(D.built) + "-" + P.length)
          .then(x => x.json());
      }
      return r;
    }).then(r => {
      C = r.careers || {};
      CV = r.curves || {};
      JC = r.jc || [];
      D.balance = r.balance || [];
      D.nbr = r.nbr || [];
      restReady = true;
      return r;
    });
  }
  return restPromise;
}

function withRest(m, draw) {
  if (restReady) return draw(m);
  m.innerHTML = '<div class="wrap"><div class="card"><p class="mut">Loading…</p></div></div>';
  const want = view;
  loadRest().then(() => {
    if (view === want || (view.v === want.v && view.id === want.id)) draw($("main"));
  });
}

function render() {
  const m = $("main");
  m.scrollTop = 0;
  m.onscroll = null;
  if (view.v === "player") withRest(m, mm => renderPlayer(mm, view.id));
  else if (view.v === "tour") withRest(m, mm => renderTour(mm, view.id));
  else if (view.v === "tours") renderTours(m);
  else if (view.v === "inst") withRest(m, mm => renderInst(mm, view.id));
  else if (view.v === "insts") withRest(m, renderInsts);
  else if (view.v === "judges") withRest(m, renderJudges);
  else if (view.v === "judge") withRest(m, mm => renderJudge(mm, view.id));
  else if (view.v === "balance") withRest(m, renderBalance);
  else if (view.v === "disclaimer") renderDisclaimer(m);
  else renderBoard(m);
}
function renderDisclaimer(m) {
  m.innerHTML = `<div class="wrap"><div class="card"><h3>Disclaimer</h3>
    <p style="max-width:640px">This site is strictly for entertainment purposes and likely contains inaccuracies.
    Quality and completeness of data is done on a best effort basis. Debaters are classified
    by name; debaters with common names may find their data combined with those with the
    same name (apologies to my Chinese debate friends). There are currently no plans to
    resolve such inaccuracies in the near future. Please never cite this site for anything
    beyond shits and giggles. The data on this site will be (hopefully) updated once a month.
    Thank you to the BP debate community for your incredible recordkeeping.</p>
  </div></div>`;
}
const ROUTES = { board: "/", tours: "/tournaments", insts: "/institutions",
                 judges: "/judges", balance: "/motions", disclaimer: "/disclaimer" };
const slug = s => String(s ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
function pathFor(v, id) {
  if (v === "player") return `/debaters/${id}/${slug(P[id][0])}`;
  if (v === "tour") return `/tournaments/${id}/${slug(T[id].n)}`;
  if (v === "inst") return `/institutions/${encodeURIComponent(id)}`;
  if (v === "judge") return `/judges/${id}/${slug(JN[id])}`;
  return ROUTES[v] || "/";
}
let slugIdx = null;
function bySlug(kind, sl) {
  if (!slugIdx) {
    slugIdx = { player: new Map(), tour: new Map() };
    P.forEach((p, i) => { const k = slug(p[0]); if (!slugIdx.player.has(k)) slugIdx.player.set(k, i); });
    T.forEach((t, i) => { const k = slug(t.n); if (!slugIdx.tour.has(k)) slugIdx.tour.set(k, i); });
  }
  return slugIdx[kind].get(sl);
}
function resolve(kind, raw, sl) {
  const n = +raw, arr = kind === "player" ? P : T;
  const nameOf = i => kind === "player" ? P[i][0] : T[i].n;
  if (arr[n] && (!sl || slug(nameOf(n)) === sl)) return n;
  const hit = sl ? bySlug(kind, sl) : undefined;
  return hit !== undefined ? hit : (arr[n] ? n : undefined);
}
function parsePath(p) {
  const s = p.replace(/\/+$/, "").split("/").slice(1).map(x => { try { return decodeURIComponent(x); } catch { return x; } });
  if (!s[0]) return { v: "board" };
  if (s[0] === "debaters") { const i = resolve("player", s[1], s[2]); return i === undefined ? { v: "board" } : { v: "player", id: i }; }
  if (s[0] === "tournaments") { if (!s[1]) return { v: "tours" };
    const i = resolve("tour", s[1], s[2]); return i === undefined ? { v: "tours" } : { v: "tour", id: i }; }
  if (s[0] === "institutions") return s[1] ? { v: "inst", id: s[1] } : { v: "insts" };
  if (s[0] === "judges") { if (!s[1]) return { v: "judges" };
    const i = +s[1]; return JN[i] ? { v: "judge", id: i } : { v: "judges" }; }
  if (s[0] === "motions") return { v: "balance" };
  if (s[0] === "disclaimer") return { v: "disclaimer" };
  return { v: "board" };
}
function titleFor(v, id) {
  return v === "player" ? P[id][0] : v === "tour" ? T[id].n
       : v === "judge" ? JN[id]
       : v === "inst" ? (buildInsts().find(x => x.key === id) || { name: id }).name
       : { tours: "Tournaments", insts: "Institutions", judges: "Judges",
           balance: "Motions", disclaimer: "Disclaimer" }[v] || "BP Debate Directory";
}

function crumbName(f) {
  if (!f || !f.v) return null;
  if (f.v === "player") return P[f.id] ? P[f.id][0] : null;
  if (f.v === "tour") return T[f.id] ? T[f.id].n : null;
  if (f.v === "inst") return (buildInsts().find(x => x.key === f.id) || {}).name || f.id;
  if (f.v === "judge") return JN[f.id] || null;
  return { board: "debaters", tours: "tournaments", insts: "institutions",
           judges: "judges", balance: "motions", disclaimer: "disclaimer" }[f.v] || null;
}
function crumb(fallbackView, fallbackLabel) {
  const n = crumbName(history.state && history.state.from);
  return `<div class="crumb">${n
    ? `<a href="#" data-back="1">&larr; ${esc(n)}</a>`
    : `<a href="#" data-v="${fallbackView}">&larr; ${fallbackLabel}</a>`}</div>`;
}
function go(v, id, push = true) {
  const from = view && view.v ? view : null;
  view = { v, id };
  const tab = v === "player" ? "board" : v === "tour" ? "tours"
            : v === "inst" ? "insts" : v === "judge" ? "judges" : v;
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.v === tab));
  document.title = titleFor(v, id);
  if (push && location.protocol !== "file:") {
    const p = pathFor(v, id);
    if (p !== location.pathname) history.pushState({ from }, "", p);
  }
  render();
}
window.onpopstate = () => { const r = parsePath(location.pathname); go(r.v, r.id, false); };
let lastTier = null;

const TAG_GAP = 0.5;
function fitTagf() {
  const f = document.querySelector(".tagf");
  if (!f || f.clientWidth < 50) return;
  const [r0, r1] = f.querySelectorAll(".tagrow");
  if (!r0 || !r1) return;
  const labs = [...f.querySelectorAll("label")];

  const fill = (row, els) => {
    row.replaceChildren();
    els.forEach((l, i) => {
      if (i) row.appendChild(document.createTextNode(" "));
      row.appendChild(l);
    });
  };
  fill(r0, labs);
  r1.replaceChildren();
  const avail = r0.clientWidth;
  for (let px = 12; px >= 7; px -= 0.5) {
    f.style.fontSize = px + "px";
    const w = labs.map(l => l.getBoundingClientRect().width);
    const gap = px * TAG_GAP;
    const tot = w.reduce((a, b) => a + b, 0);

    let run = 0, best = null;
    for (let k = 1; k < w.length; k++) {
      run += w[k - 1];
      const a = run + (k - 1) * gap, b = tot - run + (w.length - k - 1) * gap;
      const worst = Math.max(a, b);
      if (a > avail) break;
      if (b <= avail && (!best || worst < best.worst)) best = { k, worst };
    }
    if (!best) continue;
    fill(r0, labs.slice(0, best.k));
    fill(r1, labs.slice(best.k));

    [r0, r1].forEach(r =>
      r.classList.toggle("loose", r.querySelectorAll("label").length < 2));
    return;
  }
}

function watchTagf(until) {
  const f = document.querySelector(".tagf");
  if (!f) return;
  if (f.clientWidth >= 50) return void fitTagf();
  const stop = until ?? (Date.now() + 10000);
  if (Date.now() < stop) setTimeout(() => watchTagf(stop), 120);
}
addEventListener("visibilitychange", () => {
  if (!document.hidden && view.v === "balance") watchTagf();
});
const retier = () => { lastTier = tierNow(); if (D) render(); };
addEventListener("orientationchange", () => setTimeout(retier, 150));
addEventListener("resize", () => {
  const t = tierNow();
  if (t !== lastTier) { lastTier = t; if (D) render(); }
  else if (view.v === "balance") fitTagf();
});

function onMainClick(e) {
  const hit = sel => e.target.closest(sel);
  let el;
  if ((el = hit("[data-back]"))) { e.preventDefault(); history.back(); }
  else if ((el = hit("[data-p]"))) { e.preventDefault(); go("player", +el.dataset.p); }
  else if ((el = hit("[data-t]"))) { e.preventDefault(); go("tour", +el.dataset.t); }
  else if ((el = hit("[data-v]"))) { e.preventDefault(); go(el.dataset.v); }
  else if ((el = hit("tr[data-x]"))) { const x = $("x" + el.dataset.x); x.hidden = !x.hidden; }
  else if ((el = hit("tr[data-jx]"))) { const x = $("jx" + el.dataset.jx); x.hidden = !x.hidden; }
  else if ((el = hit("[data-rd]"))) { e.preventDefault(); showMotion(+el.dataset.rd); }
  else if ((el = hit("[data-xm]"))) { e.preventDefault(); showExtraMotion(+el.dataset.xm); }
  else if ((el = hit("[data-bi]"))) { e.preventDefault(); toggleBalInfo(+el.dataset.bi); }
  else if ((el = hit("tr[data-tr]"))) go("tour", +el.dataset.tr);
  else if ((el = hit("[data-in]"))) { e.preventDefault(); go("inst", el.dataset.in); }
  else if ((el = hit("[data-j]"))) { e.preventDefault(); go("judge", +el.dataset.j); }
  else if ((el = hit(".trow[data-i]"))) { e.preventDefault(); go("player", +el.dataset.i); }
  else if ((el = hit("#thead [data-k]")) && el.dataset.k !== "rank") {
    const k = el.dataset.k;
    if (sortKey === k) sortDir = -sortDir;
    else { sortKey = k; sortDir = k === "name" || k === "inst" ? 1 : -1; }
    renderBoard($("main"));
  }
  else if ((el = hit("th[data-is]"))) {
    const k = el.dataset.is;
    if (instSort.k === k) instSort.dir = -instSort.dir;
    else instSort = { k, dir: k === "name" ? 1 : -1 };
    fillInsts();
  }
  else if ((el = hit("th[data-ts]"))) {
    const k = el.dataset.ts;
    if (tourSort.k === k) tourSort.dir = -tourSort.dir;
    else tourSort = { k, dir: k === "n" ? 1 : -1 };
    fillTours();
  }
  else if ((el = hit("th[data-bs]"))) {
    const k = el.dataset.bs;
    if (balSort.k === k) balSort.dir = -balSort.dir;
    else balSort = { k, dir: k === "mo" ? 1 : -1 };
    fillBalance();
  }
  else if ((el = hit("th[data-js]"))) {
    const k = el.dataset.js;
    if (judgeSort.k === k) judgeSort.dir = -judgeSort.dir;
    else judgeSort = { k, dir: k === "name" ? 1 : -1 };
    fillJudges();
  }
}

function wire() {
  $("main").addEventListener("click", onMainClick);
  document.querySelectorAll("nav button").forEach(b => b.onclick = () => go(b.dataset.v));
  const q = $("q"), s = $("sugg");
  let items = [], sel = -1;
  const close = () => { s.style.display = "none"; sel = -1; };
  const pick = i => { close(); q.blur(); const t = items[i]; go(t.v, t.id); };
  q.oninput = () => {
    const v = q.value.trim().toLowerCase();
    if (v.length < 2) return close();

    const hits = [];
    const match = (name, alts) => {
      const k = name.indexOf(v);
      if (k === 0) return 0;
      for (const a of alts || []) if (a.toLowerCase().indexOf(v) === 0) return 1;
      if (k > 0) return 2;
      for (const a of alts || []) if (a.toLowerCase().includes(v)) return 2;
      return -1;
    };
    for (const d of derived) {
      const r = match(d.lname, aliasOf(d.i));
      if (r >= 0) hits.push({ v: "player", id: d.i, rank: r, sort: d.mu == null ? -99 : ratingOf(d).val,
                              label: d.name, meta: `${d.nt}T`, right: d.mu == null ? "—" : k1(ratingOf(d).val) });
    }
    T.forEach((t, i) => {
      const r = match(t.n.toLowerCase());
      if (r >= 0) hits.push({ v: "tour", id: i, rank: r, sort: (t.bs || {}).Open ?? -9,
                              label: t.n, meta: "tournament", right: esc(t.d) });
    });
    for (const a of (restReady ? buildInsts() : [])) {
      const r = match(a.key, akaFor(a.key));
      if (r >= 0) hits.push({ v: "inst", id: a.key, rank: r, sort: a.people,
                              label: a.name, meta: "institution", right: `${a.people} debaters` });
    }

    for (const x of (restReady ? buildJudges() : [])) {
      if (JLINK[x.j] >= 0) continue;
      const r = match(x.name.toLowerCase());
      if (r >= 0) hits.push({ v: "judge", id: x.j, rank: r, sort: x.rounds,
                              label: x.name, meta: "judge", right: `${x.rounds} rounds` });
    }
    hits.sort((a, b) => a.rank - b.rank || b.sort - a.sort);
    items = hits.slice(0, 40);
    s.innerHTML = items.length ? items.map((h, i) =>
      `<div data-s="${i}"><span>${esc(h.label)}</span><span class="m">${h.meta}</span>
       <span class="r">${h.right}</span></div>`).join("")
      : '<div class="m" style="padding:8px 10px">no match</div>';
    s.style.display = "block";
  };
  q.onkeydown = e => {
    if (e.key === "Escape") return close();
    if (!items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      [...s.children].forEach((c, i) => c.classList.toggle("sel", i === sel));
      s.children[sel]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") pick(sel < 0 ? 0 : sel);
  };
  s.onclick = e => { const t = e.target.closest("[data-s]"); if (t) pick(+t.dataset.s); };
  document.addEventListener("click", e => { if (!e.target.closest("#gsearch")) close(); });
  document.addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); q.select(); }
  });
  window.addEventListener("resize", () => { if ($("vport")) paint(); });
}
boot();
