"""'Password every start' tests. Uses throw-away settings folders (APPDATA/LOCALAPPDATA are redirected),
a test password, and a scripted password window - your real pairing and settings are never touched.
Run:  .venv\\Scripts\\python.exe tests\\test_password_gate.py"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
PY = sys.executable
PW = "Test-Pass-2026!"

tmp = tempfile.mkdtemp(prefix="acad_pwgate_")
env = dict(os.environ, APPDATA=os.path.join(tmp, "Roaming"), LOCALAPPDATA=os.path.join(tmp, "Local"))
env.pop("ACAD_MCP_PASSWORD", None)

SETUP = r"""
import sys, json; sys.path.insert(0, r'%s')
import launcher as L
secret = 'unit-test-secret-0123456789'
key = L.derive_key(secret, %r)
role = sys.argv[1]
if role == 'server':
    L.write_json(L.SERVER_CFG, {'secret': secret, 'key_enc': L.protect(key), 'port': 18765})
else:
    L.write_json(L.CLIENT_CFG, {'secret': secret, 'key_enc': L.protect(key), 'url': 'http://127.0.0.1:9/x',
                                'tools_cache': [{'name': 'autocad_status', 'inputSchema': {'type': 'object'}}]})
L.write_json(L.ROLE_CFG, {'role': role})
print('setup', role, L.CFG_DIR)
""" % (SRC, PW)


def run(code, args=(), stdin=b"", extra_env=None, timeout=120):
    e = dict(env, **(extra_env or {}))
    p = subprocess.run([PY, "-c", code, *args], input=stdin, capture_output=True, env=e, timeout=timeout)
    return p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


# ---------------------------------------------------------------- AutoCAD PC
run(SETUP, ["server"])
GATE = r"""
import sys; sys.path.insert(0, r'%s')
import launcher as L
print('GATE', L.require_password_at_start())
""" % SRC
out, _ = run(GATE, extra_env={"ACAD_MCP_PASSWORD": "wrong-password"})
check("AutoCAD PC: wrong password -> app does not open", "GATE False" in out, out[-200:])
out, _ = run(GATE, extra_env={"ACAD_MCP_PASSWORD": PW})
check("AutoCAD PC: right password -> app opens", "GATE True" in out, out[-200:])

RUNSRV = r"""
import sys; sys.path.insert(0, r'%s')
import launcher as L
L.run_server(L.load_server())
""" % SRC
out, _ = run(RUNSRV, extra_env={"ACAD_MCP_PASSWORD": "wrong-password"})
check("AutoCAD PC: wrong password -> sharing NOT started", "sharing NOT started" in out, out[-300:])

OFF = r"""
import sys; sys.path.insert(0, r'%s')
import launcher as L
d = L.read_json(L.SERVER_CFG); d['password_every_start'] = False; L.write_json(L.SERVER_CFG, d)
print('GATE', L.require_password_at_start())
""" % SRC
out, _ = run(OFF, extra_env={"ACAD_MCP_PASSWORD": "wrong-password"})
check("AutoCAD PC: setting OFF -> no password asked", "GATE True" in out, out[-200:])

# ---------------------------------------------------------------- Claude PC
for f in ("server.json", "role.json"):
    p = os.path.join(env["APPDATA"], "AutoCADRemoteMCP", f)
    if os.path.exists(p):
        os.remove(p)
run(SETUP, ["client"])
out, _ = run(GATE, extra_env={"ACAD_MCP_PASSWORD": "wrong-password"})
check("Claude PC: wrong password -> menu does not open", "GATE False" in out, out[-200:])
out, _ = run(GATE, extra_env={"ACAD_MCP_PASSWORD": PW})
check("Claude PC: right password -> menu opens", "GATE True" in out, out[-200:])

PROXY = r"""
import sys, json; sys.path.insert(0, r'%s')
import launcher as L
answers = json.loads(sys.argv[1]); shown = []
def fake_window(prompt):
    shown.append(prompt)
    return answers.pop(0) if answers else None
L.gui_password = fake_window
try:
    L.run_proxy()
finally:
    print('WINDOWS_SHOWN', len(shown), file=sys.stderr)
""" % SRC


def lines(*msgs):
    return b"".join(json.dumps(m).encode() + b"\n" for m in msgs)


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
CALL = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "autocad_status", "arguments": {}}}

# opening Claude: handshake only -> no password window, tools listed from the cache
out, err = run(PROXY, [json.dumps([])], stdin=lines(INIT, NOTE, LIST))
replies = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
check("Claude opens: no password window", "WINDOWS_SHOWN 0" in err, err[-200:])
check("Claude opens: tools still listed", any((r.get("result") or {}).get("tools") for r in replies), out[-300:])

# first AutoCAD action, wrong x3 -> refused, locked
out, err = run(PROXY, [json.dumps(["bad1", "bad2", "bad3"])], stdin=lines(INIT, CALL))
check("AutoCAD action, 3 wrong passwords -> refused", "wrong password 3 times" in out, out[-300:])

# cancel -> refused, nothing sent
out, err = run(PROXY, [json.dumps([])], stdin=lines(CALL))
check("AutoCAD action, window cancelled -> refused", "password was not entered" in out, out[-300:])

# right password -> unlocked: the request goes on to the AutoCAD PC (offline in this test)
out, err = run(PROXY, [json.dumps(["bad1", PW])], stdin=lines(CALL, dict(CALL, id=4)))
check("AutoCAD action, right password -> request sent on", "Cannot reach AutoCAD" in out and
      "password" not in out.split("Cannot reach AutoCAD")[0][-80:], out[-300:])
check("Unlocked once per session (2 requests, 1 unlock)", "WINDOWS_SHOWN 2" in err, err[-200:])

print(f"\n{sum(results)}/{len(results)} passed")
