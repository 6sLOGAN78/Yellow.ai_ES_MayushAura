import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
import websocket

dest = Path(__file__).resolve().parent
target = 'file://' + str(dest / 'decision.html')
req = urllib.request.Request('http://127.0.0.1:9224/json/new?' + urllib.parse.quote(target, safe=':/?#'), data=b'', method='PUT')
with urllib.request.urlopen(req) as response:
    tab = json.load(response)
ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=15, origin='http://127.0.0.1:9224')
seq = 0
def call(method, params=None):
    global seq
    seq += 1
    current = seq
    ws.send(json.dumps({'id': current, 'method': method, 'params': params or {}}))
    while True:
        message = json.loads(ws.recv())
        if message.get('id') == current:
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message.get('result', {})

call('Page.enable')
call('Runtime.enable')
configs = [('refusal',1200,680),('metrics-confidence',1200,680),('metrics-questions',1200,680),('decision',1280,680)]
for name, width, height in configs:
    call('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':2,'mobile':False})
    call('Page.navigate', {'url':'file://' + str(dest / (name + '.html'))})
    time.sleep(1.2)
    extra = ''
    if name == 'decision':
        extra = '.header .display{font-size:22px}.header p{font-size:12px;line-height:18px}.callout{display:none}.tile .sub{display:none}.tiles-more{margin-top:8px}.panel{gap:6px}.panel .title{font-size:14px;line-height:19px}.panel .btn-stack{gap:4px}.panel .btn{height:30px}.panel .field{gap:4px}.panel form{gap:8px!important}.tiles{padding:12px}.fold>summary{padding:8px 0}.panel .divider{margin:0}.card .lede{font-size:14px;line-height:20px}.card .small{font-size:11px}'
    elif name == 'metrics-questions':
        extra = '.header p{display:none}table.data.metrics td{font-size:17px}.table-wrap{max-height:460px;overflow:hidden}'
    expression = 'const st=document.createElement("style");st.textContent=' + json.dumps(extra) + ';document.head.appendChild(st);document.querySelectorAll(".panel details").forEach(d=>d.open=false);document.fonts.ready'
    call('Runtime.evaluate', {'expression':expression,'awaitPromise':True})
    time.sleep(.2)
    shot = call('Page.captureScreenshot', {'format':'png','fromSurface':True,'captureBeyondViewport':False})
    (dest / (name + '.png')).write_bytes(base64.b64decode(shot['data']))
    print(name, width*2, height*2)
ws.close()
