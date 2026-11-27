"""v6.1 security checks: encrypted traffic, no usable key on disk, internet mode removed.
Throw-away settings folders and a test password; a test server on 127.0.0.1:18770 (needs AutoCAD open
only for the engine to load - no drawing is changed). Run: .venv\\Scripts\\python.exe tests\\test_security.py"""

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
PY = sys.executable
PW = "Sec-Test-Pass-2026"
tmp = tempfile.mkdtemp(prefix="acad_sec_")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else ""), flush=True)


def run(code, *args, **env):
    e = dict(os.environ, APPDATA=os.path.join(tmp, "R"), LOCALAPPDATA=os.path.join(tmp, "L"))
    e.pop("ACAD_MCP_PASSWORD", None)
    e.update(env)
    p = subprocess.run([PY, "-c", code, *args], capture_output=True, env=e, timeout=180)
    return p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


HDR = "import sys, json; sys.path.insert(0, r'%s'); import launcher as L\n" % SRC

# ---------------------------------------------------------------- 2. no usable key on disk
out = run(HDR + r"""
secret = 'sec-test-secret-1234567890'
key = L.derive_key(secret, sys.argv[1])
L.write_json(L.SERVER_CFG, {'secret': secret, 'key_enc': L.protect(key), 'port': 18770})  # v6.0 format
L.write_json(L.ROLE_CFG, {'role': 'server'})
print('BEFORE', sorted(L.read_json(L.SERVER_CFG)))
cfg = L.load_server()
print('UNLOCK', L.check_password(cfg))
print('AFTER', sorted(L.read_json(L.SERVER_CFG)))
""", PW, ACAD_MCP_PASSWORD=PW)
after = out.split("AFTER", 1)[-1]
check("Old saved key is removed after the password is typed (ON)", "key_enc" not in after and "key_check" in after, out)

out = run(HDR + r"""
cfg = L.load_server()
print('KEY_WITHOUT_PASSWORD', repr(cfg['key']))
print('CONFIGURED', L.is_configured(cfg))
""")
check("Settings file alone gives no key (a copied file is useless)",
      "KEY_WITHOUT_PASSWORD None" in out and "CONFIGURED True" in out, out)

out = run(HDR + "print('OK', L.check_password(L.load_server()))", ACAD_MCP_PASSWORD="wrong-password")
check("Wrong password still refused with check-value-only storage", "OK False" in out, out)

out = run(HDR + r"""
cfg = L.load_server(); L.check_password(cfg)
d = L.read_json(L.SERVER_CFG); d['password_every_start'] = False; L.write_json(L.SERVER_CFG, d)
L.store_key(L.SERVER_CFG, L.SESSION_KEY)
print('OFF', sorted(L.read_json(L.SERVER_CFG)))
cfg2 = L.load_server(); print('KEY_LOADED', bool(cfg2['key']))
d = L.read_json(L.SERVER_CFG); d['password_every_start'] = True; L.write_json(L.SERVER_CFG, d)
L.store_key(L.SERVER_CFG, L.SESSION_KEY)
print('ON', sorted(L.read_json(L.SERVER_CFG)))
""", ACAD_MCP_PASSWORD=PW)
check("Setting OFF saves the key (DPAPI) so no password is needed",
      "key_enc" in out.split("OFF", 1)[1].split("KEY_LOADED")[0] and "KEY_LOADED True" in out, out)
check("Setting back ON removes the saved key again", "key_enc" not in out.split("ON [", 1)[1], out)

out = run(HDR + r"""
cfg = L.load_server(); cfg['folders'] = ['C:\\x']; L.save_server(cfg)   # stale dict save must not resurrect a key
print('SAVED', sorted(L.read_json(L.SERVER_CFG)))
""")
check("Saving other settings never writes a key back", "key_enc" not in out, out)

# ---------------------------------------------------------------- 10. internet mode removed
out = run(HDR + r"""
d = L.read_json(L.SERVER_CFG); d['network'] = 'internet'; L.write_json(L.SERVER_CFG, d)
print('MODE', L.net_mode())
print('MODES', sorted(L.NET_MODES))
print('GONE', all(not hasattr(L, n) for n in ('Tunnel', 'NTFY', 'TUNNEL_RE', 'ensure_cloudflared', 'doh_lookup')))
""")
check("Old 'internet' setting falls back to local network", "MODE lan" in out, out)
check("Internet mode and its Cloudflare/ntfy code are gone",
      "MODES ['lan', 'local']" in out and "GONE True" in out, out)

# ---------------------------------------------------------------- 1. encrypted traffic (live test server)
SERVER = HDR + r"""
import uvicorn, acad_server
app = acad_server.build_app(sys.argv[1], lambda: [r'%s'])
uvicorn.run(app, host='127.0.0.1', port=18770, log_level='warning')
""" % tmp
KEY = "k" * 43
log = open(os.path.join(tmp, "srv.log"), "wb")
srv = subprocess.Popen([PY, "-c", SERVER, KEY], stdout=log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, APPDATA=os.path.join(tmp, "R")))
t0 = time.time()
while time.time() - t0 < 180:
    try:
        socket.create_connection(("127.0.0.1", 18770), 0.5).close()
        break
    except OSError:
        time.sleep(1)

sys.path.insert(0, SRC)
import acad_crypto  # noqa: E402
import launcher as L  # noqa: E402

URL = "http://127.0.0.1:18770"
LIST = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()

try:
    reply = L.secure_post(URL, KEY, LIST, timeout=60)
    ok = "search_tools" in reply.decode()
except Exception as e:
    ok, reply = False, str(e).encode()
check("Encrypted request -> encrypted reply decrypts to the tool list", ok, reply[:200])


def raw_post(body, key=KEY, encrypt_header=True, ts=None, nonce=None, sign=True):
    ts = ts or f"{time.time():.3f}"
    nonce = nonce or secrets.token_hex(12)
    h = {"Content-Type": "application/octet-stream", "Accept": "application/json, text/event-stream",
         "MCP-Protocol-Version": "2025-06-18"}
    if sign:
        msg = "\n".join([ts, nonce, "POST", "/mcp", hashlib.sha256(body).hexdigest()]).encode()
        h["X-Acad-Auth"] = f"v1 {ts} {nonce} {hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()}"
    if encrypt_header:
        h[acad_crypto.ENC_HEADER] = acad_crypto.ENC_VERSION
    req = urllib.request.Request(URL + "/mcp", data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# what someone sniffing the network would see
ts, nonce = f"{time.time():.3f}", secrets.token_hex(12)
sealed = acad_crypto.seal(KEY, LIST, acad_crypto.request_aad(ts, nonce))
status, wire = raw_post(sealed, ts=ts, nonce=nonce)
check("On the wire: request and reply are unreadable",
      status == 200 and b"tools/list" not in sealed and b"search_tools" not in wire and b'"jsonrpc"' not in wire,
      f"{status} {wire[:120]!r}")

status, _ = raw_post(LIST, encrypt_header=False)
check("Signed but NOT encrypted request -> refused (426)", status == 426, status)

ts, nonce = f"{time.time():.3f}", secrets.token_hex(12)
bad = bytearray(acad_crypto.seal(KEY, LIST, acad_crypto.request_aad(ts, nonce)))
bad[20] ^= 0x01
status, _ = raw_post(bytes(bad), ts=ts, nonce=nonce)
check("Tampered encrypted request -> refused (401)", status == 401, status)

ts, nonce = f"{time.time():.3f}", secrets.token_hex(12)
status, _ = raw_post(acad_crypto.seal("wrong" * 9, LIST, acad_crypto.request_aad(ts, nonce)), key="wrong" * 9,
                     ts=ts, nonce=nonce)
check("Wrong key (wrong password) -> refused (401)", status == 401, status)

ts, nonce = f"{time.time():.3f}", secrets.token_hex(12)
body = acad_crypto.seal(KEY, LIST, acad_crypto.request_aad(ts, nonce))
first, _ = raw_post(body, ts=ts, nonce=nonce)
again, _ = raw_post(body, ts=ts, nonce=nonce)
check("Replayed request (same nonce) -> refused", first == 200 and again == 401, (first, again))

status, _ = raw_post(sealed, sign=False)
check("Unsigned request -> refused (401)", status == 401, status)

status, _ = raw_post(acad_crypto.seal(KEY, LIST, b"other"), sign=True)
check("Encrypted for another request (AAD mismatch) -> refused (401)", status == 401, status)

subprocess.run(["taskkill", "/F", "/T", "/PID", str(srv.pid)], capture_output=True)
print(f"\n{sum(results)}/{len(results)} passed")
