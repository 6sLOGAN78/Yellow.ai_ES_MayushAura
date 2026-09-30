from pathlib import Path
import json
import shutil

dest = Path(__file__).resolve().parent
source = dest.parent / 'solution/nexus_loop/static'
for name in ['app.css', 'app.js']:
    shutil.copy2(source / name, dest / name)
shutil.copytree(source / 'fonts', dest / 'fonts', dirs_exist_ok=True)
js = (source / 'app.js').read_text().replace('fetch("/report.json", { cache: "no-store" })', 'Promise.resolve(new Response(JSON.stringify(window.__CAPTURE_REPORT), {status: 200}))')
(dest / 'app.js').write_text(js)
report = json.loads((dest / 'loop-report.capture.json').read_text())
base = (source / 'index.html').read_text().replace('/static/app.css', 'app.css').replace('/static/app.js', 'app.js')
base = base.replace('<script src="app.js" defer></script>', '<script>window.__CAPTURE_REPORT=' + json.dumps(report).replace('</', '<\\/') + ';</script><script src="app.js" defer></script>')
common = '.topmeta{display:none}.topbar{gap:20px}.page{max-width:none;padding:24px}.display{font-size:30px;line-height:1.2}.pill{font-size:13px;height:24px;padding:0 8px}table.data.metrics td{font-size:16px;line-height:1.4}table.data.metrics th{font-size:14px}table.data.metrics .small{font-size:14px}table.data.metrics .pill{font-size:12px}table.data.metrics td,table.data.metrics th{padding:16px 12px}.shell{grid-template-columns:200px minmax(0,1fr)}'
configs = {
    'refusal': ('refusals/A11', '.rail{display:none}.shell{grid-template-columns:1fr}.workspace.single{grid-template-columns:minmax(0,1080px);padding:24px}.card{padding:22px}.fold-body{font-size:16px!important}.small{font-size:14px}.kv{font-size:15px}', ''),
    'metrics-confidence': ('metrics', '', ''),
    'metrics-questions': ('metrics', '', ''),
    'decision': ('findings/f01_tool_contract_northwind', '.shell{grid-template-columns:170px minmax(0,1fr)}.workspace{grid-template-columns:minmax(0,1fr) 300px;padding:20px;gap:20px}.panel{padding:16px;gap:8px}.panel .title{font-size:15px;line-height:20px}.textarea{min-height:56px;height:56px}.panel .divider{margin:4px 0}.stack{gap:12px}.card{padding:16px}.tiles{padding:16px}.header{margin-bottom:4px}.rail-item .s{display:none}.rail-item .t{font-size:12px}', 'document.querySelectorAll(".panel details").forEach(d=>d.open=false);'),
}
for name, (route, extra, setup) in configs.items():
    html = base.replace('</head>', '<style>' + common + extra + '</style></head>')
    html = html.replace('</body>', '<script>location.hash=' + json.dumps('#/' + route) + ';setTimeout(()=>{' + setup + '},800);</script></body>')
    (dest / (name + '.html')).write_text(html)
(dest / 'README.md').write_text('Source: the actual Nexus Loop console and a read-only capture copy of loop-report.json. Screenshots use a 2× pixel density. Capture-only spacing and font sizes make the product panels readable in presentation mockups. No product data or source application files are changed.\nDeck: https://www.figma.com/design/btkExuQscwEmcEJ3DxgDL5/Untitled\n')
print('Prepared four high-resolution console captures.')
