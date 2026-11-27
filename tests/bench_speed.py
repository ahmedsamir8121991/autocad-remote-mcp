"""Where does the time go? Real encrypted HTTP path to a test server on 127.0.0.1:18771 + live AutoCAD.
Opens a NEW drawing. Run: .venv\\Scripts\\python.exe tests\\bench_speed.py"""

import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)
KEY = "b" * 43
tmp = tempfile.mkdtemp(prefix="acad_bench_")
SERVER = ("import sys; sys.path.insert(0, r'%s'); import uvicorn, acad_server; "
          "uvicorn.run(acad_server.build_app(sys.argv[1], lambda: [r'%s']), host='127.0.0.1', port=18771, "
          "log_level='warning')" % (SRC, tmp))
srv = subprocess.Popen([sys.executable, "-c", SERVER, KEY], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, LOCALAPPDATA=tmp))
t0 = time.time()
while time.time() - t0 < 180:
    try:
        socket.create_connection(("127.0.0.1", 18771), 0.5).close()
        break
    except OSError:
        time.sleep(0.5)
print(f"server start (engine load): {time.time() - t0:.1f} s")

import launcher as L  # noqa: E402

URL, n = "http://127.0.0.1:18771", 0


def call(name, args):
    global n
    n += 1
    body = json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    t = time.perf_counter()
    out = L.secure_post(URL, KEY, body, timeout=120)
    return time.perf_counter() - t, len(out)


def ms(xs):
    return f"{statistics.median(xs) * 1000:.0f} ms"


call("new_drawing", {})
lst = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "tools/list"}).encode()
t = [None] * 5
for i in range(5):
    s = time.perf_counter()
    L.secure_post(URL, KEY, lst)
    t[i] = time.perf_counter() - s
print(f"transport only (tools/list, no AutoCAD): {ms(t)} per request")

single = [call("draw_line", {"x1": i * 10, "y1": 0, "x2": i * 10, "y2": 50})[0] for i in range(20)]
print(f"draw_line one call at a time: {ms(single)} per line, {sum(single):.2f} s for 20")

ops = [{"op": "draw_line", "args": {"x1": i * 10, "y1": 60, "x2": i * 10, "y2": 110}} for i in range(20)]
b, size = call("cad_batch", {"operations": ops})
print(f"   (cad_batch reply {size} bytes)")
print(f"cad_batch of the same 20 lines: {b:.2f} s total ({b / 20 * 1000:.0f} ms per line)")

eng = [call("entity_create_point", {"x": i, "y": -10})[0] for i in range(10)]
print(f"engine tool (entity_create_point): {ms(eng)} per call")

info = [call("drawing_info", {}) for _ in range(5)]
print(f"drawing_info: {ms([x[0] for x in info])}, reply {info[0][1]} bytes")
ents = call("list_entities", {})
print(f"list_entities (~50 entities): {ents[0] * 1000:.0f} ms, reply {ents[1]} bytes")
shot = call("capture_screenshot", {})
print(f"capture_screenshot: {shot[0] * 1000:.0f} ms, reply {shot[1] // 1024} KB")

subprocess.run(["taskkill", "/F", "/T", "/PID", str(srv.pid)], capture_output=True)
