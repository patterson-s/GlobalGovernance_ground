#!/usr/bin/env python3
"""unground review console — local GUI for testing annotations and logging errors.

    python3 console.py            # http://127.0.0.1:8760
    python3 console.py --port 9000

Zero dependencies (stdlib http.server). Every verdict is appended to
logs/corrections.jsonl — an error log, a gold set, and solved-cases input at once.

Verdict types
  confirm    span and id are right
  wrong_id   span right, identity wrong (chosen from candidates or search)
  reject     not a UN-system mention at all (false positive)
  missed     a mention the system did not detect (text selection + id)
  boundary   right entity, wrong span extent
"""
import argparse, json, os, re, sys, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import unground as U

LOG = os.path.join(HERE, "logs", "corrections.jsonl")
SAMPLES = os.path.join(HERE, "data", "sample")

STATE = {"doc_id": None, "text": "", "mentions": []}
_GAZ = None


def gazes():
    global _GAZ
    if _GAZ is None:
        _GAZ = U.load_gazetteer()          # (gazetteer, alias_index, alias_type)
    return _GAZ


def gazetteer():
    return gazes()[0]


def annotate(text, doc_id):
    g, alias, atype = gazes()
    ms, _ = U.scan(text, g, alias, atype, doc_id)
    ms = U.enrich(ms, g["entities"])
    return ms


def page():
    return HTML.replace("__DOCS__", json.dumps(
        sorted(f for f in os.listdir(SAMPLES) if f.endswith((".txt", ".md")))))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, page(), "text/html; charset=utf-8")
        if self.path.startswith("/api/doc"):
            name = self.path.split("name=")[-1] if "name=" in self.path else ""
            name = os.path.basename(name) or sorted(
                f for f in os.listdir(SAMPLES) if f.endswith((".txt", ".md")))[0]
            path = os.path.join(SAMPLES, name)
            if not os.path.exists(path):
                return self._send(404, json.dumps({"error": "no such doc"}))
            text = open(path, encoding="utf-8").read()
            doc_id = os.path.splitext(name)[0]
            STATE.update(doc_id=doc_id, text=text,
                         mentions=annotate(text, doc_id))
            return self._send(200, json.dumps({
                "doc_id": doc_id, "name": name, "text": text,
                "mentions": STATE["mentions"],
                "stats": summarize(STATE["mentions"])}))
        if self.path.startswith("/api/entities"):
            q = (self.path.split("q=")[-1] if "q=" in self.path else "").lower()
            g = gazetteer()
            out = []
            for eid, e in g["entities"].items():
                hay = (e["canonical"] + " " + (e.get("acronym") or "")).lower()
                if q in hay:
                    out.append({"id": eid, "canonical": e["canonical"],
                                "acronym": e.get("acronym", ""), "type": e["type"],
                                "block": e["block"],
                                "path": " > ".join(e["hierarchy_path"])})
                if len(out) >= (400 if not q else 25):
                    break
            return self._send(200, json.dumps(out))
        if self.path.startswith("/api/corrections"):
            rows = []
            if os.path.exists(LOG):
                rows = [json.loads(l) for l in open(LOG)]
            return self._send(200, json.dumps(rows[-500:]))
        self._send(404, "{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}))
        if self.path == "/api/annotate":
            text = body.get("text", "")
            doc_id = body.get("doc_id") or "pasted"
            STATE.update(doc_id=doc_id, text=text,
                         mentions=annotate(text, doc_id))
            return self._send(200, json.dumps({
                "doc_id": doc_id, "text": text, "mentions": STATE["mentions"],
                "stats": summarize(STATE["mentions"])}))
        if self.path == "/api/verdict":
            text = STATE["text"]
            s, e = int(body.get("start", 0)), int(body.get("end", 0))
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "doc_id": STATE["doc_id"],
                "start": s, "end": e,
                "surface": body.get("surface") or text[s:e],
                "verdict": body.get("verdict"),
                "system_entity_id": body.get("system_entity_id"),
                "human_entity_id": body.get("human_entity_id"),
                "system_status": body.get("system_status"),
                "system_confidence": body.get("system_confidence"),
                "decided_by": body.get("decided_by"),
                "note": body.get("note", ""),
                "context": text[max(0, s - 90):e + 90],
                "sentence": sentence(text, s, e),
            }
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return self._send(200, json.dumps({"ok": True, "n": count()}))
        if self.path == "/api/export":
            rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
            out = os.path.join(HERE, "logs", "corrections.csv")
            cols = ["ts", "doc_id", "verdict", "surface", "system_entity_id",
                    "human_entity_id", "system_status", "system_confidence",
                    "decided_by", "start", "end"]
            with open(out, "w") as f:
                f.write(",".join(cols) + "\n")
                for r in rows:
                    f.write(",".join(
                        '"' + str(r.get(c, "") or "").replace('"', '""') + '"'
                        for c in cols) + "\n")
            return self._send(200, json.dumps({"csv": out, "rows": len(rows)}))
        self._send(404, "{}")


def count():
    return sum(1 for _ in open(LOG)) if os.path.exists(LOG) else 0


def sentence(text, s, e):
    a = max(text.rfind(".", 0, s), text.rfind("\n", 0, s), text.rfind(";", 0, s))
    b = text.find(".", e)
    b = len(text) if b == -1 else b + 1
    return text[a + 1:b].strip()[:400]


def summarize(ms):
    auto = [m for m in ms if m.get("status") == "auto"]
    rev = [m for m in ms if str(m.get("status", "")).startswith("review")]
    amb = [m for m in ms if m.get("status") == "ambiguous"]
    return {"mentions": len(ms), "auto": len(auto), "review": len(rev),
            "ambiguous": len(amb)}


HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>unground — review console</title>
<style>
:root{--ink:#16181d;--mut:#6b7280;--line:#e5e2db;--paper:#fbfaf7;--card:#fff;
--accent:#1f4e79;--ok:#2e7d5b;--bad:#b23b3b;--warn:#b8770f}
*{box-sizing:border-box}
body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
color:var(--ink);background:var(--paper)}
header{display:flex;align-items:center;gap:14px;padding:10px 18px;background:var(--card);
border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{font-size:14px;margin:0;font-weight:600;letter-spacing:.02em}
select,button,input{font:inherit}
select{font-size:12px;padding:3px 6px;border:1px solid var(--line);border-radius:5px;background:#fff}
.stat{font-size:12px;color:var(--mut);display:flex;gap:10px;margin-left:auto;align-items:center}
.pill{background:#f1efe9;border-radius:20px;padding:2px 9px}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;height:calc(100vh - 49px)}
#doc{overflow:auto;padding:26px 30px;white-space:pre-wrap;font-family:Georgia,serif;font-size:16px;line-height:1.75}
#doc::-webkit-scrollbar{width:9px}
mark{background:transparent;border-bottom:2px solid var(--c,#8a8a8a);cursor:pointer;
padding:1px 0;border-radius:2px}
mark:hover{background:#fff3c4}
mark.sel{background:#ffe8a3;outline:1px solid #d3a600}
mark.ok{border-bottom-color:var(--ok);background:rgba(46,125,91,.09)}
mark.bad{border-bottom-color:var(--bad);background:rgba(178,59,59,.10);
text-decoration:line-through}
mark.amb{border-bottom-style:dotted;border-bottom-color:var(--warn)}
mark.rev{background:rgba(184,119,15,.10);border-bottom-style:dashed}
aside{background:var(--card);border-left:1px solid var(--line);overflow:auto;padding:16px 16px 40px}
aside h2{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--mut);margin:16px 0 7px}
.card{border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:#fdfcf9}
.surf{font-family:Georgia,serif;font-size:17px;margin-bottom:2px}
.meta{font-size:12px;color:var(--mut);line-height:1.5}
.path{font-size:11px;color:var(--accent);margin-top:4px}
button{background:#fff;border:1px solid var(--line);border-radius:6px;padding:5px 9px;
font-size:12px;cursor:pointer;margin:3px 3px 0 0}
button:hover{background:#f6f4ef}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
button.danger{color:var(--bad);border-color:#e6c9c9}
button.ghost{padding:3px 7px}
.cand{display:block;width:100%;text-align:left;border:1px solid var(--line);
border-radius:6px;padding:6px 8px;margin:4px 0;background:#fff;cursor:pointer;font-size:12px}
.cand:hover{background:#f0f5fa;border-color:#bcd3e8}
.cand b{font-weight:600}
.cand span{color:var(--mut);display:block;font-size:11px}
input.search{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:6px;font-size:12px}
.q{list-style:none;padding:0;margin:0;font-size:12px}
.q li{padding:6px 8px;border-bottom:1px solid #f0eee8;cursor:pointer;display:flex;gap:8px}
.q li:hover{background:#f7f5f0}
.q em{font-style:normal;color:var(--warn);font-size:10px;text-transform:uppercase}
.empty{color:var(--mut);font-size:12px;font-style:italic}
#toast{position:fixed;bottom:16px;right:400px;background:#16181d;color:#fff;
padding:8px 16px;border-radius:20px;font-size:12px;opacity:0;transition:opacity .25s;pointer-events:none}
kbd{font:11px ui-monospace;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;
padding:0 4px;background:#faf8f4}
</style></head><body>
<header>
  <h1>unground · review console</h1>
  <select id="docs"></select>
  <button class="ghost" onclick="download()">export CSV</button>
  <div class="stat">
    <span class="pill" id="s-doc">—</span>
    <span class="pill" id="s-log">0 verdicts logged</span>
    <span><kbd>j</kbd>/<kbd>k</kbd> move · <kbd>1</kbd>–<kbd>4</kbd> pick · <kbd>x</kbd> not-UN · <kbd>c</kbd> confirm</span>
  </div>
</header>
<main>
  <div id="doc"></div>
  <aside>
    <h2>selected mention</h2>
    <div id="panel" class="card"><span class="empty">Click a highlighted span in the text, or an item in the queue below. Select text to log a missed mention.</span></div>

    <h2>review queue (<span id="qn">0</span>)</h2>
    <ul class="q" id="queue"></ul>

    <h2>recent verdicts</h2>
    <ul class="q" id="recent"></ul>
  </aside>
</main>
<div id="toast"></div>
<script>
let DOC = {text:"", mentions:[], doc_id:""}, SEL = -1, VERDICTS = [];
const $ = s => document.querySelector(s);

const DOCS = __DOCS__;

async function loadDoc(name){
  const r = await fetch('/api/doc' + (name ? '?name=' + encodeURIComponent(name) : ''));
  const d = await r.json();
  DOC = d; render(); loadRecent(); renderQueue();
  $('#s-doc').textContent = `${d.stats.mentions} mentions · ${d.stats.auto} auto · ${d.stats.review+d.stats.ambiguous} review`;
  if (name) history.replaceState(null,'','#'+encodeURIComponent(name));
}

function color(m){
  if (!m.entity_id) return 'var(--warn)';
  return m.block === 'Principal Organs' ? '#c0392b'
       : m.block === 'Member States'    ? '#6c3483'
       : m.block === 'Instruments'      ? '#922b21'
       : '#1f4e79';
}

function render(){
  const ms = DOC.mentions.map((m,i)=>[m,i]).filter(([m]) => m.start != null)
                        .sort((a,b)=>a[0].start-b[0].start);
  let out = '', cur = 0;
  ms.forEach(([m, idx]) => {
    if (m.start < cur) return;
    if (m.start > cur) out += esc(DOC.text.slice(cur, m.start));
    const cls = (m.status||'').startsWith('review') ? 'rev' : (m.status==='ambiguous'?'amb':'');
    out += `<mark data-i="${idx}" class="${cls}" style="--c:${color(m)}">${esc(DOC.text.slice(m.start, m.end))}</mark>`;
    cur = m.end;
  });
  out += esc(DOC.text.slice(cur));
  $('#doc').innerHTML = out;
  document.querySelectorAll('#doc mark').forEach(el =>
    el.onclick = () => select(+el.dataset.i));
}

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function select(i){
  SEL = i;
  document.querySelectorAll('#doc mark').forEach(e => e.classList.remove('sel'));
  const el = document.querySelector(`#doc mark[data-i="${i}"]`);
  if (el){ el.classList.add('sel'); el.scrollIntoView({block:'center',behavior:'smooth'}); }
  const m = DOC.mentions[i];
  const cands = (m.entity_ids||[]).filter(Boolean);
  const state = (m.status||'') === 'auto' ? 'resolved' : m.status;
  $('#panel').innerHTML = `
    <div class="surf">${esc(m.surface)}</div>
    <div class="meta">${m.canonical ? esc(m.canonical) : '<i>unresolved</i>'}${
      m.type ? ' · ' + esc(m.type) : ''} · conf ${(m.confidence||0).toFixed(2)} · ${esc(m.decided_by||'')}</div>
    ${m.hierarchy ? `<div class="path">${esc(m.hierarchy)}</div>` : ''}
    <div class="meta" style="margin-top:6px">¶${m.para} · ${esc(state||'')}</div>
    <div style="margin-top:9px">
      ${m.entity_id ? `<button class="primary" onclick="verdict('confirm','${m.entity_id}')">Confirm <kbd>c</kbd></button>` : ''}
      <button class="danger" onclick="verdict('reject','')">Not UN <kbd>x</kbd></button>
      <button onclick="verdict('boundary','${m.entity_id||''}')">Wrong span</button>
    </div>
    <h2 style="margin-top:14px">or pick the right entity</h2>
    ${cands.map((id,n) => candButton(id, n+1)).join('') || '<span class="empty">no candidates — search below</span>'}
    <input class="search" id="q" placeholder="search ${'entities'}…" oninput="search(this.value)">
    <div id="results"></div>`;
}

function candButton(id, hotkey){
  const c = ENT.get(id) || {canonical:id, type:'', block:'', path:''};
  return `<button class="cand" onclick="verdict('wrong_id','${id}')">
    ${hotkey?`<kbd>${hotkey}</kbd> `:''}<b>${esc(c.canonical)}</b>
    <span>${esc(c.type||'')}${c.block?' · '+esc(c.block):''}${c.path?' — '+esc(c.path):''}</span></button>`;
}

let ENT = new Map();
async function preloadEntities(){
  const r = await fetch('/api/entities?q='); // all-ish
  const rows = await r.json();
  rows.forEach(e => ENT.set(e.id, e));
}

async function search(q){
  if (!q || q.length<2){ $('#results').innerHTML=''; return; }
  const r = await fetch('/api/entities?q='+encodeURIComponent(q));
  const rows = await r.json();
  rows.forEach(e => ENT.set(e.id, e));
  $('#results').innerHTML = rows.map(e =>
    `<button class="cand" onclick="verdict('wrong_id','${e.id}')"><b>${esc(e.canonical)}</b>
     <span>${esc(e.type)} · ${esc(e.block)}</span></button>`).join('')
    || '<span class="empty">no match</span>';
}

function renderQueue(){
  const q = DOC.mentions.map((m,i)=>[m,i])
    .filter(([m]) => !(m.status==='auto') || m.confidence < 0.8);
  $('#qn').textContent = q.length;
  $('#queue').innerHTML = q.slice(0,60).map(([m,i]) =>
    `<li onclick="select(${i})"><span style="flex:1">${esc(m.surface)}</span>
     <em>${esc(m.status||'')}</em></li>`).join('')
    || '<li class="empty" style="border:0">nothing in the queue</li>';
}

async function verdict(kind, human_id){
  if (SEL < 0) return;
  const m = DOC.mentions[SEL];
  const body = {verdict:kind, start:m.start, end:m.end, surface:m.surface,
    system_entity_id:m.entity_id, human_entity_id:human_id,
    system_status:m.status, system_confidence:m.confidence, decided_by:m.decided_by};
  const r = await fetch('/api/verdict', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  const d = await r.json();
  const el = document.querySelector(`#doc mark[data-i="${SEL}"]`);
  if (el){ el.classList.remove('sel','rev','amb');
    el.classList.add(kind==='reject' ? 'bad' : (kind==='confirm' ? 'ok' : 'ok')); }
  toast(`${kind.replace('_',' ')} · ${m.surface.slice(0,40)} · logged`);
  $('#s-log').textContent = d.n + ' verdicts logged';
  loadRecent();
  next();
}

function next(){
  const q = DOC.mentions.map((m,i)=>[m,i]).filter(([m])=>!m._done && !(m.status==='auto'));
  for (const [,i] of q){ if (i!==SEL){ select(i); return; } }
}

async function loadRecent(){
  const r = await fetch('/api/corrections');
  const rows = await r.json();
  $('#recent').innerHTML = rows.slice(-8).reverse().map(x =>
    `<li><span style="flex:1">${esc(x.surface)}</span><em>${esc(x.verdict)}</em></li>`).join('')
    || '<li class="empty" style="border:0">none yet</li>';
  $('#s-log').textContent = rows.length + ' verdicts logged';
}

async function download(){
  const r = await fetch('/api/export', {method:'POST', body:'{}'});
  const d = await r.json();
  toast(`wrote ${d.csv} (${d.rows} rows)`);
}

function toast(t){ const e=$('#toast'); e.textContent=t; e.style.opacity=1;
  clearTimeout(window._tt); window._tt=setTimeout(()=>e.style.opacity=0,1800); }

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const ms = DOC.mentions;
  if (e.key==='j'){ select(Math.min(SEL+1, ms.length-1)); }
  if (e.key==='k'){ select(Math.max(SEL-1, 0)); }
  if (e.key==='c' && SEL>=0 && ms[SEL].entity_id){ verdict('confirm', ms[SEL].entity_id); }
  if (e.key==='x' && SEL>=0){ verdict('reject',''); }
  if (/^[1-4]$/.test(e.key) && SEL>=0){
    const c = (ms[SEL].entity_ids||[])[+e.key-1]; if (c) verdict('wrong_id', c);
  }
});

document.addEventListener('mouseup', () => {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return;
  const txt = sel.toString().trim();
  if (txt.length < 3 || txt.length > 90) return;
  const range = sel.getRangeAt(0);
  const doc = $('#doc');
  if (!doc.contains(range.startContainer)) return;
  const pre = range.cloneRange();
  pre.selectNodeContents(doc); pre.setEnd(range.startContainer, range.startOffset);
  const start = pre.toString().length, end = start + sel.toString().length;
  toast(`selected "${txt.slice(0,40)}" — pick an entity in the panel to log a missed mention`);
  SEL = -1;
  $('#panel').innerHTML = `<div class="surf">${esc(txt)}</div>
    <div class="meta">not detected by the system — log as a missed mention</div>
    <input class="search" id="q" placeholder="search entities…" oninput="searchMiss(this.value, ${start}, ${end}, '${esc(txt).replace(/'/g,'')}')">
    <div id="results"></div>`;
});

async function searchMiss(q, start, end, surface){
  if (!q || q.length<2) return;
  const r = await fetch('/api/entities?q='+encodeURIComponent(q));
  const rows = await r.json();
  $('#results').innerHTML = rows.map(e =>
    `<button class="cand" onclick="logMissed('${e.id}', ${start}, ${end}, '${surface}')"><b>${esc(e.canonical)}</b>
     <span>${esc(e.type)} · ${esc(e.block)}</span></button>`).join('');
}

async function logMissed(id, start, end, surface){
  await fetch('/api/verdict', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({verdict:'missed', start, end, surface, human_entity_id:id})});
  toast('missed mention logged'); loadRecent(); $('#panel').innerHTML =
    '<span class="empty">logged. select another span or pick from the queue.</span>';
}

(async () => {
  $('#docs').innerHTML = DOCS.map(d => `<option>${d}</option>`).join('');
  $('#docs').onchange = e => loadDoc(e.target.value);
  await preloadEntities();
  const h = decodeURIComponent(location.hash.slice(1));
  await loadDoc(h || 'charter.txt');
})();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    gazetteer()            # warm the index before serving
    url = f"http://127.0.0.1:{a.port}/"
    print(f"unground review console → {url}\ncorrections → {LOG}")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
