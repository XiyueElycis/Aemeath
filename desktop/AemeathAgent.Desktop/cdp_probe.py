"""通过 CDP 读取桌宠 WebView2 页面运行时状态。"""
import json
import urllib.request
import websocket

pages = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5).read())
target = next((p for p in pages if p.get("type") == "page"), None)
if not target:
    raise SystemExit("no page")

ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=15)
msg_id = 0


def ev(expr):
    global msg_id
    msg_id += 1
    ws.send(json.dumps({
        "id": msg_id, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            r = resp.get("result", {}).get("result", {})
            return r.get("value") if "value" in r else r.get("description", "")


print("title:", ev("document.title"))
print("body bg:", ev("getComputedStyle(document.body).backgroundColor"))
print("canvas count:", ev("document.querySelectorAll('canvas').length"))
print("petLoaded:", ev("window.__petLoaded"))
print("petErrors:", ev("JSON.stringify(window.__petErrors)"))
print("pet api:", ev("Object.keys(window.pet||{})"))
print("pixi live2d:", ev("typeof PIXI.live2d"))
print("stage child:", ev("document.getElementById('stage').childElementCount"))
print("hint visible:", ev("document.getElementById('hint') && getComputedStyle(document.getElementById('hint')).display"))
print("app view exists:", ev("!!(window.__app)"))
ws.close()
