"""The pairing secret is DPAPI-encrypted on disk (v6.2.1). Throw-away settings folders; your real setup is
never touched. Run: .venv\\Scripts\\python.exe tests\\test_secret_at_rest.py"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
PY = sys.executable
tmp = tempfile.mkdtemp(prefix="acad_secret_")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else ""), flush=True)


def run(code):
    e = dict(os.environ, APPDATA=os.path.join(tmp, "R"), LOCALAPPDATA=os.path.join(tmp, "L"))
    e.pop("ACAD_MCP_PASSWORD", None)
    p = subprocess.run([PY, "-c", "import sys, json; sys.path.insert(0, r'%s'); import launcher as L\n" % SRC + code],
                       capture_output=True, env=e, timeout=120)
    return p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


SECRET = "unit-secret-QWERTY-0123456789"
raw_path = lambda name: os.path.join(tmp, "R", "AutoCADRemoteMCP", name)

# 1. new files: secret never written in plain text, read back unchanged
for name, cfg in (("server.json", "SERVER_CFG"), ("client.json", "CLIENT_CFG")):
    out = run(f"L.write_json(L.{cfg}, {{'secret': {SECRET!r}, 'port': 1}}); print('READ', L.read_json(L.{cfg})['secret'])")
    raw = open(raw_path(name), encoding="utf-8").read()
    check(f"{name}: secret not stored in plain text", SECRET not in raw and '"secret_enc"' in raw, raw[:200])
    check(f"{name}: secret reads back correctly", f"READ {SECRET}" in out, out[-300:])

# 2. key derivation still gives the same key (pairing keeps working)
out = run(f"cfg = L.read_json(L.SERVER_CFG); print('SAME', L.derive_key(cfg['secret'], 'pw-12345678') == L.derive_key({SECRET!r}, 'pw-12345678'))")
check("Same access key as before (existing pairings keep working)", "SAME True" in out, out[-300:])

# 3. a v6.2.0 file with a plain secret is upgraded on first read
os.makedirs(os.path.dirname(raw_path("server.json")), exist_ok=True)
with open(raw_path("server.json"), "w", encoding="utf-8") as f:
    json.dump({"secret": SECRET, "key_check": "abc", "port": 8765}, f)
out = run("print('READ', L.read_json(L.SERVER_CFG)['secret'], sorted(L.read_json(L.SERVER_CFG)))")
raw = open(raw_path("server.json"), encoding="utf-8").read()
check("Old plain file: still reads correctly", f"READ {SECRET}" in out, out[-300:])
check("Old plain file: encrypted on disk after first read", SECRET not in raw and '"secret_enc"' in raw and '"key_check"' in raw, raw)

# 4. a file copied to another PC / account cannot reveal the secret
with open(raw_path("server.json"), "w", encoding="utf-8") as f:
    json.dump({"secret_enc": "bm90LWEtZHBhcGktYmxvYg==", "key_check": "abc", "port": 8765}, f)
out = run("c = L.read_json(L.SERVER_CFG); print('HAS', 'secret' in c, 'CONFIGURED', L.is_configured(c))")
check("Unreadable secret (copied file) -> treated as not set up, no crash", "HAS False CONFIGURED False" in out, out[-300:])

# 5. role.json and other files are untouched
out = run("L.write_json(L.ROLE_CFG, {'role': 'server', 'secret': 'not-special'}); print(open(L.ROLE_CFG).read())")
check("Other settings files are written as before", '"secret": "not-special"' in out, out[-300:])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
