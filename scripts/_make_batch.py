"""Generate a Chrome MCP browser_batch JSON for B-R box-score scrapes."""
import json
import sys

TAB_ID = int(sys.argv[1])
games = json.loads(sys.argv[2])

JS_TPL = (
    "(() => { const filename = '__FNAME__'; "
    "const esc = s => '\"' + String(s ?? '').replace(/\"/g,'\"\"') + '\"'; "
    "const tableToCsv = t => { const L = []; for (const tr of t.querySelectorAll('tr')) "
    "{ if (tr.classList.contains('over_header')||tr.classList.contains('spacer')) continue; "
    "const c = Array.from(tr.querySelectorAll('th,td')); if (!c.length) continue; "
    "L.push(c.map(x => (x.textContent||'').replace(/\\s+/g,' ').trim()).map(esc).join(',')); } "
    "return L.join('\\n'); }; const out = {}; let i = 46; const seen = new Set(); "
    "for (const t of document.querySelectorAll('table.stats_table')) "
    "{ const cap = t.querySelector('caption')?.textContent?.trim()||''; if (!cap) continue; "
    "const csv = tableToCsv(t); if (seen.has(csv)) continue; seen.add(csv); "
    "out[`idx${i}__`+cap.replace(/[^A-Za-z0-9]+/g,'_').replace(/^_+|_+$/g,'')] = csv; i++; } "
    "let meta=''; const sb=document.querySelector('.scorebox_meta'); "
    "if (sb) meta=sb.innerText.replace(/\\n\\s*\\n/g,'\\n').trim(); "
    "if (Object.keys(out).length<4) return {ok:false, count:Object.keys(out).length, title:document.title}; "
    "const json = JSON.stringify({url:location.href,title:document.title,tables:out,scorebox_meta:meta}); "
    "const blob = new Blob([json],{type:'application/json'}); const u=URL.createObjectURL(blob); "
    "const a=document.createElement('a'); a.href=u; a.download=filename; document.body.appendChild(a); "
    "a.click(); setTimeout(()=>{URL.revokeObjectURL(u);a.remove();},1500); "
    "return {ok:true, filename, len:json.length, tables:Object.keys(out)}; })()"
)

actions = []
for g in games:
    rc = g["home_rc"]
    date = g["date"]
    sfx = g["sfx"]
    url = f"https://www.baseball-reference.com/boxes/{rc}/{rc}{date}{sfx}.shtml"
    fname = f"bref_boxscore_{rc}{date}{sfx}.json"
    js = JS_TPL.replace("__FNAME__", fname)
    actions.append({"name": "navigate", "input": {"url": url, "tabId": TAB_ID}})
    actions.append({"name": "javascript_tool", "input": {"action": "javascript_exec", "tabId": TAB_ID, "text": js}})

print(json.dumps(actions))
