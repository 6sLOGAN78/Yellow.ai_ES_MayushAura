from pathlib import Path
import json
import shutil

run = Path(__file__).resolve().parent
root = run.parent
assets = run / 'composition' / 'assets'
assets.mkdir(exist_ok=True)
for sub in ['fonts', 'ui', 'music', 'sfx', 'vendor']:
    (assets / sub).mkdir(exist_ok=True)
src = root / 'solution' / 'nexus_loop' / 'static'
for font in src.joinpath('fonts').glob('*.woff2'):
    shutil.copy2(font, assets / 'fonts' / font.name)
shutil.copy2(src / 'app.css', assets / 'ui' / 'app.css')
shutil.copytree(src / 'fonts', assets / 'ui' / 'fonts', dirs_exist_ok=True)
js = (src / 'app.js').read_text().replace('fetch("/report.json", { cache: "no-store" })', 'Promise.resolve(new Response(JSON.stringify(window.__BRAG_REPORT), {status: 200}))')
(assets / 'ui' / 'app.js').write_text(js)
base = (src / 'index.html').read_text().replace('/static/app.css', 'app.css').replace('/static/app.js', 'app.js')
rep = json.loads((root / 'solution' / 'out' / 'loop-report.json').read_text())
for p in rep.get('prescriptions', []):
    p.pop('approval', None)
for state in ['finding', 'approved']:
    fixture = json.loads(json.dumps(rep))
    if state == 'approved':
        fixture['prescriptions'][0]['approval'] = {'verdict': 'accepted', 'decided_by': 'Product team', 'reason': 'Replay improved the cohort; all 30 known-good sessions stayed clean.', 'at': '2026-09-30T08:00:00Z'}
    html = base.replace('<script src="app.js" defer></script>', '<script>window.__BRAG_REPORT = ' + json.dumps(fixture).replace('</', '<\\/') + ';</script><script src="app.js" defer></script>')
    if state == 'approved':
        setup = 'document.querySelectorAll(".panel details").forEach(d=>d.open=false);'
    else:
        setup = 'document.querySelectorAll(".panel details").forEach(d=>d.open=false); document.getElementById("who").value="Product team"; document.getElementById("reason").value="Replay improved the cohort; all 30 known-good sessions stayed clean.";'
    html = html.replace('</body>', '<script>setTimeout(()=>{' + setup + '},500);</script></body>')
    (assets / 'ui' / (state + '.html')).write_text(html)
skill_assets = Path('/home/logan78/.codex/skills/brag/assets')
music = 'happy-beats-business-moves-vol-12-by-ende-dot-app.mp3'
shutil.copy2(skill_assets / 'music' / music, assets / 'music' / 'bed.mp3')
for name in ['drop_001.ogg', 'select_008.ogg']:
    shutil.copy2(skill_assets / 'sfx' / 'interface' / name, assets / 'sfx' / name)
print('Prepared actual product UI fixtures, local fonts, music and sound effects.')
