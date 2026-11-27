"""End-to-end check that 'password every start' does not get in the way of real use:
the BUILT exe shares AutoCAD (THIS PC mode, test port 18765), the connector runs exactly as Claude starts it,
and Claude-style requests draw in the real AutoCAD. Throw-away settings folders + a test password:
your real pairing and settings are not touched.
Run: .venv\\Scripts\\python.exe tests\\e2e_password.py"""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
EXE = os.path.join(ROOT, "dist", "AutoCADRemoteMCP.exe")
PY = sys.executable
PW = "E2e-Test-Pass-2026"
PORT = 18765
tmp = tempfile.mkdtemp(prefix="acad_e2e_")
drawings = os.path.join(tmp, "drawings")
os.makedirs(drawings)


def env_for(name, **extra):
    e = dict(os.environ, APPDATA=os.path.join(tmp, name, "Roaming"), LOCALAPPDATA=os.path.join(tmp, name, "Local"))
    e.pop("ACAD_MCP_PASSWORD", None)
    e.update(extra)
    return e


SETUP = r"""
import sys; sys.path.insert(0, r'%s')
import launcher as L
secret = 'e2e-secret-abcdefghijklmnop'
key = L.derive_key(secret, %r)
if sys.argv[1] == 'server':
    L.write_json(L.SERVER_CFG, {'secret': secret, 'key_enc': L.protect(key), 'port': %d, 'network': 'local',
                                'folders': [r'%s']})
    L.write_json(L.ROLE_CFG, {'role': 'server'})
else:
    L.write_json(L.CLIENT_CFG, {'secret': secret, 'key_enc': L.protect(key), 'url': 'http://127.0.0.1:%d',
                                'tools_cache': [{'name': 'autocad_status', 'description': 'cached',
                                                 'inputSchema': {'type': 'object', 'properties': {}}}]})
    L.write_json(L.ROLE_CFG, {'role': 'client'})
""" % (SRC, PW, PORT, drawings, PORT)
for role in ("server", "client"):
    subprocess.run([PY, "-c", SETUP, role], env=env_for(role), check=True)

ok_all = []


def check(name, ok, detail=""):
    ok_all.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else ""), flush=True)


def listening():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


# 1. AutoCAD PC: wrong password -> sharing must NOT start
p = subprocess.run([EXE, "--server"], env=env_for("server", ACAD_MCP_PASSWORD="wrong-password"),
                   capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
out = p.stdout.decode("utf-8", "replace")
check("exe --server, wrong password: sharing not started", "sharing NOT started" in out and not listening(), out[-300:])

# 2. AutoCAD PC: right password -> sharing starts
srv_log = open(os.path.join(tmp, "server.log"), "wb")  # a file, not a pipe: an unread pipe fills and freezes it
srv = subprocess.Popen([EXE, "--server"], env=env_for("server", ACAD_MCP_PASSWORD=PW),
                       stdout=srv_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
print("server log:", os.path.join(tmp, "server.log"), flush=True)
t0 = time.time()
while time.time() - t0 < 180 and not listening():
    time.sleep(1)
check(f"exe --server, right password: sharing started ({time.time() - t0:.0f} s)", listening())

# 3. Claude PC: the connector, started the way Claude starts it ('password every start' ON)
PROXY = r"""
import sys; sys.path.insert(0, r'%s')
import launcher as L
shown = []
def window(prompt):
    shown.append(prompt); print('[test] password window shown:', prompt.splitlines()[0], file=sys.stderr, flush=True)
    return %r
L.gui_password = window
L.run_proxy()
""" % (SRC, PW)
cli_err = open(os.path.join(tmp, "proxy.log"), "w+b")
cli = subprocess.Popen([PY, "-c", PROXY], env=env_for("client"), stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=cli_err)
nid = [0]


def ask(method, params=None, timeout=120):
    nid[0] += 1
    msg = {"jsonrpc": "2.0", "id": nid[0], "method": method}
    if params is not None:
        msg["params"] = params
    cli.stdin.write(json.dumps(msg).encode() + b"\n")
    cli.stdin.flush()
    t = time.time()
    while time.time() - t < timeout:
        line = cli.stdout.readline()
        if not line:
            break
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id") == nid[0]:
            return r
    return {}


def text_of(r):
    res = r.get("result") or {}
    return " ".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")


def call(name, args=None):
    return ask("tools/call", {"name": name, "arguments": args or {}})


r = ask("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "e2e", "version": "1"}})
check("Claude opens: connector answers initialize", bool(r.get("result")), str(r)[:200])
cli.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n")
cli.stdin.flush()
r = ask("tools/list")
check("Claude opens: tools listed, no password window yet", bool((r.get("result") or {}).get("tools")), str(r)[:200])

r = call("autocad_status")
status = text_of(r)
check("First AutoCAD action: password window -> unlocked -> AutoCAD answers",
      '"connected": true' in status.replace("\n", "").replace("  ", " ") or "connected" in status, status[:300])

r = ask("tools/list")
names = [t["name"] for t in (r.get("result") or {}).get("tools", [])]
check(f"After unlock: real tool list from the AutoCAD PC ({len(names)} listed)",
      "search_tools" in names and "cad_batch" in names, str(names)[:200])

r = call("new_drawing")
check("new_drawing", not (r.get("result") or {}).get("isError"), text_of(r)[:200])
r = call("draw_rectangle", {"x1": 0, "y1": 0, "x2": 200, "y2": 100})
check("draw_rectangle 200 x 100", '"closed": true' in text_of(r), text_of(r)[:200])
r = call("add_text", {"x": 20, "y": 45, "text": "PASSWORD TEST OK", "height": 12})
check("add_text", not (r.get("result") or {}).get("isError"), text_of(r)[:200])
r = call("call_tool", {"name": "gd_frame", "arguments": {"x": 120, "y": 120, "symbol": "flatness",
                                                          "tolerance": 0.05}})
check("engine tool via tool search (gd_frame)", '"ok":true' in text_of(r).replace(" ", ""), text_of(r)[:200])
r = call("zoom_extents")
r = call("drawing_info")
info = text_of(r)
check("drawing_info shows the new entities", "AcDbPolyline" in info and "AcDbText" in info, info[:300])
r = call("capture_screenshot")
imgs = [c for c in (r.get("result") or {}).get("content", []) if c.get("type") == "image"]
if imgs:
    with open(os.path.join(HERE, "e2e_autocad.png"), "wb") as f:
        f.write(base64.b64decode(imgs[0]["data"]))
check("capture_screenshot of AutoCAD", bool(imgs))

cli.stdin.close()
try:
    cli.wait(70)
except subprocess.TimeoutExpired:
    cli.kill()
cli_err.seek(0)
err = cli_err.read().decode("utf-8", "replace")
windows = err.count("password window shown")
check(f"Password window shown exactly once for the whole session ({windows})", windows == 1, err[-400:])

hist = os.path.join(tmp, "server", "Local", "AutoCADRemoteMCP", "history")
logged = ""
if os.path.isdir(hist):
    for f in os.listdir(hist):
        logged += open(os.path.join(hist, f), encoding="utf-8").read()
check("AutoCAD PC: lasting history file records the session's tools",
      "draw_rectangle" in logged and "new_drawing" in logged, hist)

for role in ("server", "client"):
    p = os.path.join(tmp, role, "Roaming", "AutoCADRemoteMCP", f"{role}.json")
    saved = json.load(open(p, encoding="utf-8"))
    check(f"{role} PC: no usable key left on disk after the session",
          "key_enc" not in saved and "key_check" in saved, sorted(saved))

# a one-file exe runs as two processes (unpacker + app): stop the whole tree, then any test copy left
subprocess.run(["taskkill", "/F", "/T", "/PID", str(srv.pid)], capture_output=True)
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='AutoCADRemoteMCP.exe'\" | "
                f"Where-Object {{ $_.ExecutablePath -eq '{EXE}' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
print(f"\n{sum(ok_all)}/{len(ok_all)} passed")
