"""
AutoCAD Remote MCP  -  one exe, two roles, protected by YOUR password
=====================================================================

  AutoCAD PC : shares AutoCAD on the LOCAL NETWORK (or this PC only) - nothing goes over the internet.
  Claude PC  : installs a small connector into Claude Desktop / Claude Code that
               finds the AutoCAD PC on the local network automatically.

Security model
  - Connecting needs BOTH the pairing code and the password you choose.
  - Every request is signed AND encrypted (AES-256-GCM) with a key derived from the password (scrypt);
    replies are encrypted too. Unencrypted requests are refused.
  - The password itself is never stored or transmitted. With 'password every start' (default) a PC keeps
    only a check value - the key is rebuilt from the password each time and lives in memory only.
    With it off, the key is saved encrypted with Windows DPAPI (this Windows account on this PC only).
  - Pairing code / password changes / uninstall on the AutoCAD PC ask for the password.
  - The server locks out an address for 15 minutes after 5 wrong attempts.

Double-click the exe and pick a role. Command line (advanced):
  --setup             show the setup menu
  --server            run the AutoCAD-side server (used by the Windows startup shortcut)
  --pair CODE         set up this PC as the Claude side (asks for the password)
  --proxy             stdio bridge started by Claude (do not run by hand)
  --uninstall         remove startup shortcut and Claude config entries
  --port N            local port for the server (default 8765)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import glob
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = "AutoCADRemoteMCP"
VERSION = "6.2.1"
SERVER_NAME = "autocad-remote"
UA = f"{APP}/{VERSION}"

LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
ROAMING = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
INSTALL_DIR = LOCAL / APP
INSTALLED_EXE = INSTALL_DIR / f"{APP}.exe"
CLOUDFLARED = INSTALL_DIR / "cloudflared.exe"
CFG_DIR = ROAMING / APP
SERVER_CFG = CFG_DIR / "server.json"
CLIENT_CFG = CFG_DIR / "client.json"
ROLE_CFG = CFG_DIR / "role.json"
STARTUP_LNK = ROAMING / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AutoCAD Remote MCP.lnk"

CODE_PREFIX = "ACAD-"
MIN_PASSWORD = 8
FROZEN = getattr(sys, "frozen", False)
SCRYPT = dict(n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


# =============================================================================
# small helpers
# =============================================================================

def _holds_secret(path: Path) -> bool:
    """server.json / client.json hold the pairing secret; it is kept DPAPI-encrypted on disk."""
    return path in (SERVER_CFG, CLIENT_CFG)


def read_json(path: Path, default=None):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {} if default is None else default
    if isinstance(data, dict) and _holds_secret(path):
        if "secret_enc" in data:
            try:
                data["secret"] = unprotect(data.pop("secret_enc"))
            except Exception:
                # Copied from another PC or another Windows account: the secret cannot be read here,
                # so this PC behaves as not set up (choose [1] or [2] again).
                data.pop("secret", None)
        elif data.get("secret"):
            try:
                write_json(path, data)  # file from v6.2.0 or older: encrypt its plain secret now
            except Exception:
                pass
    return data


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict) and _holds_secret(path) and data.get("secret"):
        data = dict(data)
        try:
            data["secret_enc"] = protect(data["secret"])
            del data["secret"]
        except Exception:
            pass  # DPAPI unavailable: keep the plain secret so the app keeps working as before
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def line(ch="=") -> str:
    return ch * 74


def pause() -> None:
    if FROZEN and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass


def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 20, method=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run("clip", input=text.encode("utf-8"), check=True, shell=True)
        return True
    except Exception:
        return False


# ---- crypto ------------------------------------------------------------------

def derive_key(secret: str, password: str) -> str:
    """Password + pairing secret -> access key. Slow on purpose (scrypt) to resist guessing."""
    raw = hashlib.scrypt(password.encode("utf-8"), salt=("acadmcp:" + secret).encode(), **SCRYPT)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def protect(text: str) -> str:
    """Encrypt with Windows DPAPI: only this Windows user on this PC can decrypt it."""
    import win32crypt
    return base64.b64encode(win32crypt.CryptProtectData(text.encode(), APP, None, None, None, 0)).decode()


def unprotect(blob: str) -> str:
    import win32crypt
    return win32crypt.CryptUnprotectData(base64.b64decode(blob), None, None, None, 0)[1].decode()


def topic_for(secret: str) -> str:
    return "acadmcp-" + hashlib.sha256(("topic:" + secret).encode()).hexdigest()[:32]


def sign(key: str, url: str) -> str:
    return hmac.new(key.encode(), url.encode(), hashlib.sha256).hexdigest()[:32]


def secure_post(url: str, key: str, payload: bytes, proto: str = "2025-06-18", timeout: float = 300) -> bytes:
    """Send one MCP message to the AutoCAD PC: encrypted (AES-256-GCM) and signed with the access key, which
    never travels. The reply is decrypted and checked; an unencrypted or altered reply is rejected.
    Raises urllib.error.HTTPError for refusals (401 wrong password, 429 locked, 426 old AutoCAD PC app)."""
    import acad_crypto
    ts, nonce = f"{time.time():.3f}", secrets.token_hex(12)
    body = acad_crypto.seal(key, payload, acad_crypto.request_aad(ts, nonce))
    msg = "\n".join([ts, nonce, "POST", "/mcp", hashlib.sha256(body).hexdigest()]).encode()
    sig = hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url + "/mcp", data=body, method="POST", headers={
        "User-Agent": UA, "X-Acad-Auth": f"v1 {ts} {nonce} {sig}",
        acad_crypto.ENC_HEADER: acad_crypto.ENC_VERSION, "Content-Type": "application/octet-stream",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": proto})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get(acad_crypto.ENC_HEADER) or "") == acad_crypto.ENC_VERSION
    if not raw:
        return b""
    if not enc:
        raise PermissionError("the AutoCAD PC answered without encryption - refused (update its app)")
    return acad_crypto.open_sealed(key, raw, acad_crypto.reply_aad(nonce))


# ---- local-network discovery (no internet involved) ------------------------------

DISCOVERY_PORT = 8766
NET_MODES = {"lan": "LOCAL NETWORK only", "local": "THIS PC only"}  # internet mode removed in v6.1


def net_mode() -> str:
    """The saved network mode; anything else (e.g. the removed 'internet') falls back to the local network."""
    mode = read_json(SERVER_CFG).get("network", "lan")
    return mode if mode in NET_MODES else "lan"


def lan_responder(bind_ip: str, port: int, secret: str, key: str) -> None:
    """Answer 'where is the AutoCAD PC?' broadcasts from paired Claude PCs on the local network."""
    want = b"ACADMCP-DISCOVER " + topic_for(secret).encode()
    reply = f"ACADMCP-HERE {port} {sign(key, f'lan|{port}')}".encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((bind_ip, DISCOVERY_PORT))
    while True:
        try:
            data, addr = s.recvfrom(512)
            if data.strip() == want:
                s.sendto(reply, addr)
        except Exception:
            time.sleep(0.5)


def lan_discover(secret: str, key: str, timeout: float = 1.5) -> tuple[str | None, bool]:
    """Find the AutoCAD PC on this PC or the local network. Only an answer signed with the key is accepted.
    Returns (url, answered): answered=True when the AutoCAD PC for this pairing code replied but its signature
    didn't match this key - i.e. the password is wrong."""
    answered = False
    msg = b"ACADMCP-DISCOVER " + topic_for(secret).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:  # Windows: don't turn "nobody listening" ICMP replies into errors on this socket
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    except Exception:
        pass
    s.settimeout(0.3)
    targets = {"127.0.0.1", "255.255.255.255"}
    try:
        for info in _orig_getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                targets.add(".".join(ip.split(".")[:3] + ["255"]))  # /24 subnet broadcast
    except Exception:
        pass
    try:
        for t in targets:
            try:
                s.sendto(msg, (t, DISCOVERY_PORT))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(512)
            except (socket.timeout, ConnectionResetError, OSError):
                continue
            parts = data.decode(errors="replace").split()
            if len(parts) == 3 and parts[0] == "ACADMCP-HERE" and parts[1].isdigit():
                if hmac.compare_digest(parts[2], sign(key, f"lan|{parts[1]}")):
                    return f"http://{addr[0]}:{int(parts[1])}", True
                answered = True
    finally:
        s.close()
    return None, answered


def lan_ips() -> list[str]:
    ips = set()
    try:
        for info in _orig_getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                ips.add(info[4][0])
    except Exception:
        pass
    return sorted(ips)


def add_firewall_rules(port: int) -> bool:
    """Allow the server port and the discovery port on PRIVATE networks only (needs admin; best effort)."""
    ok = True
    for proto, p, name in (("TCP", port, "AutoCAD Remote MCP"), ("UDP", DISCOVERY_PORT, "AutoCAD Remote MCP discovery")):
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in", "action=allow",
                            f"protocol={proto}", f"localport={p}", "profile=private"], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = ok and r.returncode == 0
    return ok


def _env_password() -> str | None:
    return os.environ.get("ACAD_MCP_PASSWORD")  # for unattended/scripted setups only


def ask_password(prompt: str = "  Password: ") -> str:
    pw = _env_password()
    if pw is not None:
        return pw
    return getpass.getpass(prompt)


def ask_new_password() -> str:
    pw = _env_password()
    if pw is not None:
        return pw
    print(f"  Choose a password only you know (at least {MIN_PASSWORD} characters).")
    print("  Typing is hidden. You will need it once on each Claude PC.")
    while True:
        p1 = getpass.getpass("  New password:     ")
        if len(p1) < MIN_PASSWORD:
            print(f"  Too short - use at least {MIN_PASSWORD} characters.\n")
            continue
        if p1.lower() in ("password", "12345678", "123456789", "autocad1", "qwertyui"):
            print("  That password is too easy to guess.\n")
            continue
        if getpass.getpass("  Repeat password:  ") != p1:
            print("  The passwords don't match. Try again.\n")
            continue
        return p1


_orig_getaddrinfo = socket.getaddrinfo  # (internet-mode DNS fallback removed in v6.1)


# ---- install helpers --------------------------------------------------------------

def install_self() -> Path:
    """Copy this exe to %LOCALAPPDATA%\\AutoCADRemoteMCP so shortcuts/config point to a stable place."""
    if not FROZEN:
        return Path(sys.executable)
    me = Path(sys.executable).resolve()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    if me != INSTALLED_EXE.resolve():
        try:
            shutil.copy2(me, INSTALLED_EXE)
        except PermissionError:
            print("  (installed copy is in use - keeping the existing one)")
    return INSTALLED_EXE


def exe_command(args: list[str]) -> tuple[str, list[str]]:
    """Command + args that start this program (frozen exe, or python + script when run from source)."""
    if FROZEN:
        return str(INSTALLED_EXE if INSTALLED_EXE.exists() else Path(sys.executable)), args
    return sys.executable, [str(Path(__file__).resolve())] + args


def stop_other_instances() -> None:
    """Stop an already-running AutoCAD-side server (and an old v5/v6.0 tunnel) so new settings take effect."""
    me = {os.getpid(), os.getppid()}
    paths = [str(INSTALLED_EXE), str(CLOUDFLARED)]
    if FROZEN:
        paths.append(str(Path(sys.executable).resolve()))
    ps = ("Get-CimInstance Win32_Process | Where-Object { $p = $_.ExecutablePath; $p -and (@(" +
          ",".join("'" + p.replace("'", "''") + "'" for p in paths) +
          ") -contains $p) -and (@(" + ",".join(str(i) for i in me) + ") -notcontains $_.ProcessId) } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)


# =============================================================================
# AutoCAD PC side
# =============================================================================

SESSION_KEY = ""  # the access key rebuilt from the password typed in this process (memory only)


def key_matches(cfg: dict, key: str) -> bool:
    """Is `key` this pairing's access key? Checked against the saved key or, if only that is saved, the check value."""
    import acad_crypto
    if not key:
        return False
    if cfg.get("key_check"):
        return hmac.compare_digest(acad_crypto.key_check(key), cfg["key_check"])
    if cfg.get("key_enc"):
        try:
            return hmac.compare_digest(unprotect(cfg["key_enc"]), key)
        except Exception:
            return False
    return False


def store_key(path: Path, key: str) -> None:
    """Save how this PC remembers the key. 'Password every start' ON: only the check value (the key itself is
    never on disk - a copied settings file can't be used without the password). OFF: the key too, encrypted
    with Windows DPAPI, so no password is needed to start."""
    import acad_crypto
    data = read_json(path)
    data["key_check"] = acad_crypto.key_check(key)
    if password_every_start(path):
        data.pop("key_enc", None)
    else:
        data["key_enc"] = protect(key)
    write_json(path, data)


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("secret") and (cfg.get("key_enc") or cfg.get("key_check")))


def load_server() -> dict:
    """Server settings with the usable key under 'key' (None when not set up, or when the key is not saved
    and the password has not been typed in this window yet)."""
    cfg = read_json(SERVER_CFG)
    cfg.setdefault("port", 8765)
    cfg["key"] = None
    if cfg.get("secret") and cfg.get("key_enc"):
        try:
            cfg["key"] = unprotect(cfg["key_enc"])
        except Exception:
            print("  [!] Could not unlock the saved settings (different Windows user?). Run the setup again.")
    elif cfg.get("secret") and SESSION_KEY and key_matches(cfg, SESSION_KEY):
        cfg["key"] = SESSION_KEY
    return cfg


def save_server(cfg: dict) -> None:
    """Save settings. How the key is stored is owned by store_key: never overwritten from a stale copy here."""
    current = read_json(SERVER_CFG)
    out = {k: v for k, v in cfg.items() if k not in ("key", "token", "key_enc", "key_check")}
    for k in ("key_enc", "key_check"):
        if k in current:
            out[k] = current[k]
    write_json(SERVER_CFG, out)


def default_drawings_folder() -> str:
    try:
        from win32com.shell import shell, shellcon
        docs = shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0)
    except Exception:
        docs = str(Path.home() / "Documents")
    return str(Path(docs) / "AutoCAD Remote")


def server_folders() -> list[str]:
    """The only folder(s) Claude may open/save drawings in. Read fresh each time so changes apply at once."""
    return read_json(SERVER_CFG).get("folders") or [default_drawings_folder()]


def choose_network_mode() -> None:
    cfg = load_server()
    if not is_configured(cfg):
        print("\n  Set up this PC as the AutoCAD PC first (choose [1]).")
        pause()
        return
    current = net_mode()
    print("\n  Network mode decides WHO can reach AutoCAD on this PC (nothing ever goes over the internet):")
    print("    L  LOCAL NETWORK only - Claude PCs on the same Wi-Fi/LAN  [default]")
    print("    P  THIS PC only       - only Claude running on this same computer  [most private]")
    print(f"\n  Current mode: {NET_MODES[current]}\n")
    if not check_password(cfg):
        print("  Access denied.")
        pause()
        return
    ans = input("  Type L or P (Enter = keep): ").strip().lower()
    new = {"l": "lan", "p": "local"}.get(ans)
    if not new:
        print("  No change.")
        pause()
        return
    cfg["network"] = new
    save_server(cfg)
    print(f"  [ok] Network mode set to {NET_MODES[new]}.")
    if new == "lan":
        if add_firewall_rules(int(cfg["port"])):
            print("  [ok] Firewall opened for PRIVATE networks only.")
        else:
            print("  [!] Could not change the firewall (needs admin). When Windows asks, tick 'Private networks'")
            print("      and click 'Allow access'. Keep the network set to 'Private' in Windows settings.")
    if port_in_use(int(cfg["port"])):
        print("  Restarting sharing so the new mode takes effect...\n")
        stop_other_instances()
        run_server(load_server())
    else:
        pause()


def choose_access_mode() -> None:
    cfg = load_server()
    if not is_configured(cfg):
        print("\n  Set up this PC as the AutoCAD PC first (choose [1]).")
        pause()
        return
    current = bool(cfg.get("read_only"))
    print("\n  Access mode decides what Claude may do with AutoCAD on this PC:")
    print("    FULL       - draw, edit, save, export PDF (normal use)")
    print("    READ-ONLY  - only look, measure, search, take screenshots; nothing can be changed")
    print(f"\n  Current mode: {'READ-ONLY' if current else 'FULL'}\n")
    if not check_password(cfg):
        print("  Access denied.")
        pause()
        return
    ans = input("  Type F for FULL or R for READ-ONLY (Enter = keep): ").strip().lower()
    if ans not in ("f", "r"):
        print("  No change.")
        pause()
        return
    cfg["read_only"] = ans == "r"
    save_server(cfg)
    print(f"  [ok] Access mode set to {'READ-ONLY' if cfg['read_only'] else 'FULL'}.")
    if port_in_use(int(cfg["port"])):
        print("  Restarting sharing so the new mode takes effect...\n")
        stop_other_instances()
        run_server(load_server())
    else:
        pause()


def choose_folder() -> None:
    cfg = load_server()
    if not is_configured(cfg):
        print("\n  Set up this PC as the AutoCAD PC first (choose [1]).")
        pause()
        return
    print("\n  For security, Claude can open and save drawings ONLY inside one folder.")
    print(f"  Current folder:  {server_folders()[0]}\n")
    if not check_password(cfg):
        print("  Access denied.")
        pause()
        return
    raw = input("  Paste the full path of the folder to use (or press Enter for the default):\n  > ")
    path = raw.strip().strip('"') or default_drawings_folder()
    p = Path(os.path.expandvars(path))
    if not p.is_absolute() or str(p).startswith("\\\\"):
        print("  Please give a full local path, like C:\\Users\\you\\Documents\\Plans")
        pause()
        return
    if p.anchor.rstrip("\\/") and str(p).rstrip("\\/") == p.anchor.rstrip("\\/"):
        print("  A whole drive is too broad - choose a folder.")
        pause()
        return
    p.mkdir(parents=True, exist_ok=True)
    cfg["folders"] = [str(p)]
    save_server(cfg)
    print(f"  [ok] Drawings folder set to: {p}")
    print("       This applies immediately, also to the sharing window that is already running.")
    pause()


UNLOCKED = False  # the password was typed correctly in this window (asked once per window, not per step)


def password_every_start(path: Path) -> bool:
    """'Ask for the password every time the app starts' - on unless turned off in the menu."""
    return bool(read_json(path).get("password_every_start", True))


def check_password(cfg: dict, tries: int = 3) -> bool:
    """AutoCAD PC: check the password; on success the rebuilt key goes into cfg['key'] and memory, and the
    saved form is brought in line with 'password every start' (an old saved key is removed when it is on)."""
    global UNLOCKED, SESSION_KEY
    if UNLOCKED and SESSION_KEY:  # already typed correctly in this window
        cfg["key"] = cfg.get("key") or SESSION_KEY
        return True
    for i in range(tries):
        pw = ask_password("  Enter your password: ")
        k = derive_key(cfg["secret"], pw)
        if key_matches(cfg, k):
            UNLOCKED, SESSION_KEY = True, k
            cfg["key"] = k
            store_key(SERVER_CFG, k)
            return True
        print("  Wrong password." + ("" if i == tries - 1 else " Try again. (Typing is hidden - nothing shows on screen.)"))
        if _env_password() is not None:
            break
        time.sleep(2)
    return False


def client_key(cfg: dict) -> str:
    """Claude PC: the usable key - saved (DPAPI) when 'password every start' is off, else the one typed now."""
    if cfg.get("key_enc"):
        try:
            return unprotect(cfg["key_enc"])
        except Exception:
            return ""
    if SESSION_KEY and key_matches(cfg, SESSION_KEY):
        return SESSION_KEY
    return ""


def check_client_password(cfg: dict, tries: int = 3) -> bool:
    """Claude PC: the password is right when it rebuilds this pairing's access key."""
    global UNLOCKED, SESSION_KEY
    if not is_configured(cfg) or (UNLOCKED and SESSION_KEY):
        return True  # not paired yet (nothing to protect), or already typed in this window
    for i in range(tries):
        pw = ask_password("  Enter your password: ")
        k = derive_key(cfg["secret"], pw)
        if key_matches(cfg, k):
            UNLOCKED, SESSION_KEY = True, k
            store_key(CLIENT_CFG, k)
            return True
        print("  Wrong password." + ("" if i == tries - 1 else " Try again. (Typing is hidden.)"))
        if _env_password() is not None:
            break
        time.sleep(2)
    return False


def require_password_at_start() -> bool:
    """Gate for opening the app (menu or sharing) when 'password every start' is on for this PC's role."""
    if UNLOCKED:
        return True
    role = read_json(ROLE_CFG).get("role")
    if role == "server" and password_every_start(SERVER_CFG):
        cfg = load_server()
        if not is_configured(cfg):
            return True
        print(line())
        print("  AutoCAD Remote MCP is protected: your password is required to open it.")
        print(line())
        return check_password(cfg)
    if role == "client" and password_every_start(CLIENT_CFG):
        cfg = read_json(CLIENT_CFG)
        if not is_configured(cfg):
            return True
        print(line())
        print("  AutoCAD Remote MCP is protected: your password is required to open it.")
        print(line())
        return check_client_password(cfg)
    return True


def gui_password(prompt: str) -> str | None:
    """Small always-on-top password window (Claude starts the connector without a console)."""
    import tkinter as tk
    res = {"pw": None}
    root = tk.Tk()
    root.title("AutoCAD Remote MCP")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    tk.Label(root, text=prompt, padx=18, pady=12, justify="left", font=("Segoe UI", 10)).pack()
    entry = tk.Entry(root, show="•", width=34, font=("Segoe UI", 11))
    entry.pack(padx=18)
    bar = tk.Frame(root)
    bar.pack(pady=12)

    def ok(*_):
        res["pw"] = entry.get()
        root.destroy()

    def cancel(*_):
        root.destroy()

    tk.Button(bar, text="Unlock", width=11, command=ok).pack(side="left", padx=5)
    tk.Button(bar, text="Cancel", width=11, command=cancel).pack(side="left", padx=5)
    root.bind("<Return>", ok)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.eval("tk::PlaceWindow . center")
    root.after(80, lambda: (root.lift(), root.focus_force(), entry.focus_force()))
    root.mainloop()
    return res["pw"]


def toggle_password_every_start() -> None:
    role = read_json(ROLE_CFG).get("role")
    if role not in ("server", "client"):
        print("\n  Set up this PC first (choose [1] or [2]).")
        pause()
        return
    path = SERVER_CFG if role == "server" else CLIENT_CFG
    now = password_every_start(path)
    print("\n  Password every start:")
    print("    ON   - the password is asked each time the app opens" +
          (" and each time sharing starts (also at Windows login)." if role == "server" else
           ", and once per Claude session before Claude can use AutoCAD."))
    print("           The key is NOT saved on this PC - only a check value (safest).")
    print("    OFF  - the password is asked only for settings; the key is saved, encrypted for this")
    print("           Windows account (anything running as you could use it).")
    print(f"\n  Current: {'ON' if now else 'OFF'}\n")
    ok = check_password(load_server()) if role == "server" else check_client_password(read_json(CLIENT_CFG))
    if not ok:
        print("  Access denied.")
        pause()
        return
    ans = input("  Type ON or OFF (Enter = keep): ").strip().lower()
    if ans not in ("on", "off"):
        print("  No change.")
        pause()
        return
    data = read_json(path)
    data["password_every_start"] = ans == "on"
    write_json(path, data)
    store_key(path, SESSION_KEY)  # ON: remove the saved key; OFF: save it (DPAPI)
    print(f"  [ok] Password every start: {ans.upper()}." +
          ("  The key is no longer saved on this PC." if ans == "on" else ""))
    if role == "client":
        print("  Fully quit Claude (tray icon -> Quit) and open it again for this to apply.")
    elif port_in_use(int(data.get("port", 8765))):
        print("  Applies the next time sharing starts.")
    pause()


def remove_old_tunnel_files() -> None:
    """Internet mode was removed in v6.1: delete the Cloudflare tunnel program an older version downloaded."""
    for f in (CLOUDFLARED, CLOUDFLARED.with_suffix(".part")):
        try:
            if f.exists():
                f.unlink()
                print("  [ok] Removed the old internet-tunnel program (internet mode no longer exists)")
        except OSError:
            pass


def make_startup_shortcut() -> None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    cmd, args = exe_command(["--server"])
    sc = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(STARTUP_LNK))
    sc.TargetPath = cmd
    sc.Arguments = subprocess.list2cmdline(args)
    sc.WorkingDirectory = str(Path(cmd).parent)
    sc.WindowStyle = 7  # minimized
    sc.Description = "Shares AutoCAD with Claude"
    sc.Save()


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_server(cfg: dict) -> None:
    os.system("title AutoCAD Remote MCP - sharing AutoCAD")
    if not is_configured(cfg):
        # never set up, or set up with an older version that had no password: create one now
        print(line())
        print("  A PASSWORD is needed before AutoCAD can be shared from this PC.")
        print(line())
        return setup_server(False)
    port = int(cfg["port"])
    if port_in_use(port):
        return menu()  # already sharing in another window: offer the menu instead of a dead end
    # The password is needed when 'password every start' is on, and always when the key isn't saved on this PC.
    if (password_every_start(SERVER_CFG) or not cfg.get("key")) and not (UNLOCKED and cfg.get("key")):
        print(line())
        print("  Your password is required to start sharing AutoCAD.")
        print(line())
        if not check_password(cfg):
            print("  Access denied - sharing NOT started.")
            pause()
            return

    import uvicorn
    from acad_server import build_app

    read_only = bool(read_json(SERVER_CFG).get("read_only"))
    if read_json(SERVER_CFG).get("network") not in (None, *NET_MODES):
        print("  [i] Internet mode was removed for security - sharing on the LOCAL NETWORK instead.")
    mode = net_mode()
    host = "0.0.0.0" if mode == "lan" else "127.0.0.1"
    discovery = read_json(SERVER_CFG).get("discovery", "search")
    server = uvicorn.Server(uvicorn.Config(build_app(cfg["key"], server_folders, read_only=read_only,
                                                     discovery=discovery),
                                           host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    threading.Thread(target=lan_responder, args=(host, port, cfg["secret"], cfg["key"]), daemon=True).start()

    print(line())
    print(f"  AutoCAD Remote MCP v{VERSION}  -  sharing AutoCAD on {socket.gethostname()}")
    print(line())
    print("  Protected by your password. Pairing code is hidden here; to see it run")
    print("  the exe with --setup, choose [1] and enter your password.")
    print()
    print(f"  Drawings folder (the only place Claude can open/save files):")
    print(f"     {server_folders()[0]}")
    print("  Safe mode: no LISP, scripts or free-form commands.")
    print("  Access: " + ("READ-ONLY (Claude can look and measure, but not change anything)"
                          if read_only else "FULL (Claude can draw and edit)"))
    print()
    print(f"  Network: {NET_MODES.get(mode, mode)}" + {
        "lan": f"  -  this PC: {', '.join(lan_ips()) or '?'} (no internet used)",
        "local": "  -  only Claude on this same PC can connect"}.get(mode, ""))
    print("  Encryption: every request and reply is encrypted (AES-256-GCM).")
    print("  Keep this window open (you can minimize it). Close it to stop sharing.")
    print(line())
    time.sleep(1.5)
    print("  [ready] ONLINE on the " + ("local network" if mode == "lan" else "this PC") +
          " - Claude can now reach AutoCAD", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def setup_server(change: bool = False, port: int | None = None) -> None:
    cfg = load_server()
    if port:
        cfg["port"] = port
    configured = is_configured(cfg)
    print("\n  " + ("Changing password / pairing code..." if change else "Setting up THIS PC as the AutoCAD PC...") + "\n")
    if configured:
        if not check_password(cfg):
            print("  Access denied.")
            pause()
            return
    elif change:
        print("  This PC is not set up as the AutoCAD PC yet - choose [1] first.")
        pause()
        return
    changed = change or not configured
    if changed:
        if change:
            print("\n  Set the NEW password. The old pairing code and password will stop working.")
        pw = ask_new_password()
        cfg["secret"] = secrets.token_urlsafe(18)
        cfg["key"] = derive_key(cfg["secret"], pw)
        save_server(cfg)
        store_key(SERVER_CFG, cfg["key"])
        global UNLOCKED, SESSION_KEY
        UNLOCKED, SESSION_KEY = True, cfg["key"]  # just typed it: don't ask again before sharing starts
        print("  [ok] Password set (the password is never saved" +
              ("; the key isn't either - only a check value)" if password_every_start(SERVER_CFG) else ")"))
    already_running = port_in_use(int(cfg["port"]))
    if changed or not already_running:
        stop_other_instances()  # an older running copy would block the update and keep old settings
        install_self()
        remove_old_tunnel_files()
        mode = net_mode()
        if mode == "lan":
            if add_firewall_rules(int(cfg["port"])):
                print("  [ok] Firewall opened for PRIVATE networks only")
            else:
                print("  [i] If Windows asks about the firewall: tick 'Private networks' and click 'Allow access'.")
        try:
            make_startup_shortcut()
            print("  [ok] Starts automatically when you log in to Windows")
        except Exception as e:
            print(f"  [!] Could not create startup shortcut: {e}")
        write_json(ROLE_CFG, {"role": "server"})
    code = CODE_PREFIX + cfg["secret"]
    copy_to_clipboard(code)
    print(line())
    print("  YOUR PAIRING CODE (copied to clipboard):\n")
    print(f"     {code}\n")
    print("  On the Claude PC you will need this code AND your password.")
    print("  Keep both private.")
    print(line())
    if already_running and not changed:
        print("  AutoCAD sharing is already running in its own window - nothing else to do.")
        print("  You can close THIS window now.")
        pause()
        return
    print("  Starting the server now...\n")
    run_server(cfg)


# =============================================================================
# Claude PC side
# =============================================================================

def resolve_url(secret: str, key: str) -> tuple[str | None, bool]:
    """Find the AutoCAD PC on this PC or the local network (only an answer signed with the key counts; nothing
    goes over the internet). Returns (url, answered): answered without a url means the password is wrong."""
    return lan_discover(secret, key)


def check_remote(url: str, key: str) -> tuple[bool, str]:
    try:
        http(url + "/health", timeout=15)
    except Exception as e:
        return False, f"AutoCAD PC not reachable ({e})"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "autocad_status", "arguments": {}}}).encode()
    try:
        out = secure_post(url, key, body, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "wrong password or pairing code"
        if e.code == 429:
            return False, "too many wrong attempts - locked for 15 minutes"
        if e.code == 426:
            return False, "the AutoCAD PC needs encryption - update AutoCADRemoteMCP on this PC"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"secure connection failed ({e})"
    res = json.loads(out).get("result", {})
    txt = (res.get("content") or [{}])[0].get("text", "")
    if res.get("isError"):
        return True, "connected, but " + txt.replace("Error executing tool autocad_status: ", "")
    try:
        info = json.loads(txt)
        return True, f"connected to {info.get('server_pc')} - AutoCAD {info.get('autocad_version')}"
    except Exception:
        return True, "connected"


def desktop_config_paths() -> list[Path]:
    found: set[Path] = set()
    for pat in (str(ROAMING / "Claude" / "claude_desktop_config.json"),
                str(LOCAL / "Claude-Data" / "*" / "claude_desktop_config.json"),
                str(LOCAL / "Packages" / "*Claude*" / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")):
        found.update(Path(p) for p in glob.glob(pat))
    for d in glob.glob(str(LOCAL / "Claude-Data" / "*")):  # new-style desktop app data folders
        if Path(d, "config.json").exists():
            found.add(Path(d) / "claude_desktop_config.json")
    if (ROAMING / "Claude").is_dir():
        found.add(ROAMING / "Claude" / "claude_desktop_config.json")
    if not found:
        found.add(ROAMING / "Claude" / "claude_desktop_config.json")
    return sorted(found)


def _edit_json(path: Path, fn) -> None:
    data = read_json(path, default={}) if path.exists() else {}
    if path.exists():
        bak = path.with_name(path.name + ".bak-autocad")
        if not bak.exists():
            shutil.copy2(path, bak)
    fn(data)
    write_json(path, data)


def write_claude_configs(remove: bool = False) -> list[str]:
    cmd, args = exe_command(["--proxy"])
    done = []

    def apply(data, entry):
        servers = data.setdefault("mcpServers", {})
        if remove:
            servers.pop(SERVER_NAME, None)
        else:
            servers[SERVER_NAME] = entry

    for p in desktop_config_paths():
        if remove and not p.exists():
            continue
        try:
            _edit_json(p, lambda d: apply(d, {"command": cmd, "args": args}))
            done.append(f"Claude Desktop  ({p})")
        except Exception as e:
            print(f"  [!] {p}: {e}")
    code_cfg = Path.home() / ".claude.json"
    if code_cfg.exists():
        try:
            _edit_json(code_cfg, lambda d: apply(d, {"type": "stdio", "command": cmd, "args": args, "env": {}}))
            done.append(f"Claude Code     ({code_cfg})")
        except Exception as e:
            print(f"  [!] {code_cfg}: {e}")
    return done


def setup_client(code: str | None, write_configs: bool = True) -> None:
    print("\n  Setting up THIS PC as the Claude PC...\n")
    while not code:
        code = input("  Paste the pairing code from the AutoCAD PC and press Enter:\n  > ").strip()
    code = code.strip().strip('"')
    if not code.startswith(CODE_PREFIX) or len(code) < len(CODE_PREFIX) + 20:
        print("  That doesn't look like a pairing code (it starts with ACAD-).")
        pause()
        return
    secret = code[len(CODE_PREFIX):]
    url = None
    for attempt in range(3):
        pw = ask_password("  Enter your password (typing is hidden): ")
        print("  Checking...")
        key = derive_key(secret, pw)
        url, seen = resolve_url(secret, key)
        if url or not seen:
            break
        print("  [X] Wrong password or pairing code.")
        if attempt == 2 or _env_password() is not None:
            print("  Nothing was saved.")
            pause()
            return
    install_self()
    if url:
        ok, msg = check_remote(url, key)
        print(f"  [{'ok' if ok else '!'}] {msg}")
        if not ok and "wrong password" in msg:
            print("  Nothing was saved.")
            pause()
            return
    else:
        print("  [!] The AutoCAD PC is not online right now, so the password can't be checked yet.")
        print("      It will connect automatically once AutoCADRemoteMCP is running there.")
    write_json(CLIENT_CFG, {"secret": secret, "url": url})
    store_key(CLIENT_CFG, key)  # 'password every start' (default ON): only a check value, never the key
    write_json(ROLE_CFG, {"role": "client"})
    print("  [ok] Paired. The password is never saved" +
          ("; the key isn't either - you'll type the password once per Claude session."
           if password_every_start(CLIENT_CFG) else "; the key is saved encrypted for your Windows account."))
    if write_configs:
        for t in write_claude_configs():
            print(f"  [ok] Added '{SERVER_NAME}' to {t}")
    print(line())
    print("  DONE. Fully quit Claude (tray icon -> Quit) and open it again.")
    print("  Then ask Claude: \"Check the AutoCAD status and draw a 100x50 rectangle.\"")
    print(line())
    pause()


# ---- stdio proxy (started by Claude) ---------------------------------------

def run_proxy() -> None:
    out_lock = threading.Lock()
    cfg = read_json(CLIENT_CFG)
    secret = cfg.get("secret", "")
    saved = ""
    if cfg.get("key_enc") and not password_every_start(CLIENT_CFG):
        try:
            saved = unprotect(cfg["key_enc"])
        except Exception:
            saved = ""
    # The key used for this Claude session. With 'password every start' it is rebuilt from the password the user
    # types in the password window and lives only in this process's memory.
    state = {"url": cfg.get("url"), "proto": "2025-06-18", "key": saved}
    url_lock = threading.Lock()
    gate = {"open": bool(saved) or not is_configured(cfg), "until": 0.0}
    gate_lock = threading.Lock()

    def log(*a):
        print("[autocad-remote]", *a, file=sys.stderr, flush=True)

    def unlock() -> None:
        if gate["open"]:
            return
        with gate_lock:  # one password window at a time, even if Claude sends several requests
            if gate["open"]:
                return
            if time.time() < gate["until"]:
                raise PermissionError("locked for 1 minute after 3 wrong passwords - try again shortly")
            prompt = "Claude wants to use AutoCAD.\nEnter your AutoCAD Remote MCP password:"
            for attempt in range(3):
                try:
                    pw = gui_password(prompt)
                except Exception as e:
                    raise PermissionError(f"could not show the password window ({e})")
                if pw is None:
                    raise PermissionError("the password was not entered - AutoCAD stays locked until the user "
                                          "unlocks it (they can retry the same request)")
                k = derive_key(secret, pw)
                if key_matches(cfg, k):
                    state["key"] = k
                    gate["open"] = True
                    if cfg.get("key_enc") and password_every_start(CLIENT_CFG):
                        store_key(CLIENT_CFG, k)  # an older version saved the key: remove it now
                        cfg.update(read_json(CLIENT_CFG))
                        cfg.pop("key_enc", None)
                    log("unlocked for this Claude session")
                    return
                prompt = f"Wrong password ({attempt + 1} of 3). Try again:"
            gate["until"] = time.time() + 60
            raise PermissionError("wrong password 3 times - locked for 1 minute")

    def emit(obj) -> None:
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        with out_lock:
            sys.stdout.buffer.write(data.strip() + b"\n")
            sys.stdout.buffer.flush()

    def refresh_url(stale: str | None) -> str | None:
        with url_lock:
            if state["url"] != stale:  # another thread already refreshed it
                return state["url"]
            new, _ = resolve_url(secret, state["key"])
            if new and new != cfg.get("url"):
                cfg["url"] = new
                data = read_json(CLIENT_CFG)
                data["url"] = new
                write_json(CLIENT_CFG, data)
            state["url"] = new
            log("AutoCAD PC address:", new)
            return new

    def post(raw: bytes) -> bytes:
        if not state["key"]:
            raise PermissionError("this PC is not paired - run AutoCADRemoteMCP.exe and choose [2]")
        url = state["url"] or refresh_url(None)
        for attempt in range(2):
            if not url:
                raise ConnectionError("the AutoCAD PC was not found on the local network")
            try:
                return secure_post(url, state["key"], raw, state["proto"], timeout=300)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise PermissionError("password rejected - the password or pairing code was changed; "
                                          "run AutoCADRemoteMCP.exe on this PC and choose [2] again")
                if e.code == 429:
                    raise PermissionError("too many wrong password attempts - locked for 15 minutes")
                if e.code == 426:
                    raise PermissionError("the AutoCAD PC requires encryption - update AutoCADRemoteMCP on this PC")
                if e.code in (404, 502, 503, 504):
                    if attempt == 0:
                        url = refresh_url(url)
                        continue
                    raise ConnectionError("the AutoCAD PC is offline (its sharing window is not running)")
                raise
            except (urllib.error.URLError, OSError):
                if attempt == 0:
                    url = refresh_url(url)
                    continue
                raise ConnectionError("the AutoCAD PC was not found on the local network")
        raise ConnectionError("the AutoCAD PC is offline")

    def fallback(msg: dict, err: Exception) -> None:
        mid, method = msg.get("id"), msg.get("method")
        text = (f"Cannot reach AutoCAD: {err}. Ask the user to start AutoCADRemoteMCP.exe on the AutoCAD PC "
                f"(it starts at Windows login and asks for the password) and that both PCs are on the same network."
                if isinstance(err, ConnectionError) else f"Cannot use AutoCAD: {err}.")
        if method == "initialize":
            emit({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", state["proto"]),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                "instructions": "AutoCAD PC currently unavailable. " + text}})
        elif method == "tools/list":
            emit({"jsonrpc": "2.0", "id": mid, "result": {"tools": cfg.get("tools_cache", [])}})
        elif method == "tools/call":
            emit({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": True}})
        elif method == "ping":
            emit({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            emit({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": text}})

    def handle(raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        is_req = isinstance(msg, dict) and "id" in msg and "method" in msg
        method = msg.get("method") if isinstance(msg, dict) else None
        if not gate["open"]:
            # Claude's start-up handshake is answered here, so opening Claude never pops the password window;
            # it only appears when Claude actually wants to use AutoCAD.
            if not is_req:
                return  # notifications (e.g. initialized): the AutoCAD PC's server is stateless
            if method == "initialize":
                emit({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "protocolVersion": (msg.get("params") or {}).get("protocolVersion", state["proto"]),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                    "instructions": "AutoCAD Remote is password-protected: on the first AutoCAD action the user is "
                                    "asked for their password in a small window on this PC."}})
                return
            if method == "ping":
                emit({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                return
            if method == "tools/list" and cfg.get("tools_cache"):
                emit({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": cfg["tools_cache"]}})
                return
            try:
                unlock()
            except PermissionError as e:
                log("locked:", e)
                fallback(msg, e)
                return
        try:
            body = post(raw)
            if not body.strip():
                return
            emit(body)
            if is_req:
                res = json.loads(body).get("result") or {}
                if msg["method"] == "initialize" and res.get("protocolVersion"):
                    state["proto"] = res["protocolVersion"]
                if msg["method"] == "tools/list" and res.get("tools"):
                    cfg["tools_cache"] = res["tools"]
                    data = read_json(CLIENT_CFG)  # update only the cache; key storage is store_key's job
                    data["tools_cache"] = res["tools"]
                    write_json(CLIENT_CFG, data)
        except Exception as e:
            log("error:", e)
            if is_req:
                fallback(msg, e)

    workers: list[threading.Thread] = []
    for raw in iter(sys.stdin.buffer.readline, b""):
        if raw.strip():
            t = threading.Thread(target=handle, args=(raw,), daemon=True)
            t.start()
            workers = [w for w in workers if w.is_alive()] + [t]
    for t in workers:  # input closed: let in-flight requests finish
        t.join(timeout=60)


# =============================================================================
# uninstall / menu / main
# =============================================================================

def uninstall() -> None:
    print("\n  Removing AutoCAD Remote MCP from this PC...")
    cfg = load_server()
    if is_configured(cfg):
        print("  This PC shares AutoCAD - your password is required.")
        if not check_password(cfg):
            print("  Access denied.")
            pause()
            return
        stop_other_instances()
    if STARTUP_LNK.exists():
        STARTUP_LNK.unlink()
        print("  [ok] Removed startup shortcut")
    remove_old_tunnel_files()
    for t in write_claude_configs(remove=True):
        print(f"  [ok] Removed '{SERVER_NAME}' from {t}")
    for f in (CLIENT_CFG, SERVER_CFG, ROLE_CFG, CFG_DIR / "config.json"):
        if f.exists():
            f.unlink()
    print("  [ok] Removed saved keys and settings")
    print("  Done. (You can delete the folder %LOCALAPPDATA%\\AutoCADRemoteMCP)")
    pause()


def menu() -> None:
    os.system("title AutoCAD Remote MCP - setup")
    if not require_password_at_start():
        print("  Access denied.")
        pause()
        return
    role = read_json(ROLE_CFG).get("role")
    print(line())
    print(f"  AutoCAD Remote MCP v{VERSION}  -  connect AutoCAD to Claude")
    print(line())
    if role == "server":
        running = port_in_use(int(read_json(SERVER_CFG).get("port", 8765)))
        print("  This PC is the AUTOCAD PC.  Sharing is " +
              ("RUNNING in another window." if running else "NOT running (choose [1] to start it)."))
        print(line("-"))
    if role == "client":
        cfg = read_json(CLIENT_CFG)
        print("  This PC is set up as the CLAUDE PC. Checking the AutoCAD PC...")
        try:
            key = client_key(cfg)  # the saved key, or the one rebuilt from the password typed above
            if not key:
                msg = "Locked - the status check needs the password (Claude will ask for it when needed)."
            else:
                url, seen = resolve_url(cfg["secret"], key)
                msg = check_remote(url, key)[1] if url else (
                    "Password no longer matches - choose [2] again." if seen else
                    "AutoCAD PC not found on the local network right now.")
        except Exception:
            msg = "Not paired correctly - choose [2]."
        print("  " + msg)
        print(line("-"))
    print("  [1]  This PC has AUTOCAD   (start sharing / show pairing code)")
    print("  [2]  This PC has CLAUDE    (connect to the AutoCAD PC)")
    print("  [3]  Change password       (AutoCAD PC - makes a new pairing code)")
    print("  [4]  Uninstall")
    print("  [5]  Drawings folder       (AutoCAD PC - where Claude may open/save files)")
    print("  [6]  Access mode           (AutoCAD PC - FULL or READ-ONLY)")
    print("  [7]  Network mode          (AutoCAD PC - LOCAL NETWORK / THIS PC only)")
    if role in ("server", "client"):
        on = password_every_start(SERVER_CFG if role == "server" else CLIENT_CFG)
        print(f"  [8]  Password every start  (now {'ON' if on else 'OFF'})")
    else:
        print("  [8]  Password every start  (set up this PC first)")
    print("  [9]  Exit")
    choice = input("\n  Choose 1-9 and press Enter: ").strip()
    if choice == "1":
        setup_server(False)
    elif choice == "2":
        setup_client(None)
    elif choice == "3":
        setup_server(True)
    elif choice == "4":
        uninstall()
    elif choice == "5":
        choose_folder()
    elif choice == "6":
        choose_access_mode()
    elif choice == "7":
        choose_network_mode()
    elif choice == "8":
        toggle_password_every_start()
    elif choice != "9":
        print("  Please type a number from 1 to 9.")
        time.sleep(2)
        return menu()


def main() -> None:
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--server", action="store_true")
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--pair")
    ap.add_argument("--no-claude-config", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--port", type=int)
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if a.selftest:  # load every tool (app + AutoCAD MCP Pro engine), print a summary, exit. Touches no config.
        import asyncio
        import tempfile
        from acad_server import build_app, mcp as _mcp
        build_app(secrets.token_hex(32), lambda: [tempfile.gettempdir()])
        names = sorted(t.name for t in asyncio.run(_mcp.list_tools()))
        listed = asyncio.run(_mcp._mcp_server.request_handlers[__import__("mcp").types.ListToolsRequest](
            __import__("mcp").types.ListToolsRequest(method="tools/list")))
        print(f"v{VERSION}: {len(names)} tools, {len(listed.root.tools)} listed up front (tool search on)")
        hits = asyncio.run(_mcp.call_tool("search_tools", {"query": "FILLET", "limit": 3}))
        hits = hits[0] if isinstance(hits, tuple) else hits
        print("  search FILLET:", (getattr(hits[0], "text", "") or "")[:200].replace("\n", " "))
        for must in ("draw_line", "gd_frame", "sheet_frame", "arch_wall", "pid_line_draw", "layer_isolate"):
            print(f"  {must}: {'ok' if must in names else 'MISSING'}")
        r = asyncio.run(_mcp.call_tool("std_part_list", {}))  # reads the engine's bundled ISO tables
        r = r[0] if isinstance(r, tuple) else r
        blocks = getattr(r, "content", r)
        print("  std_part_list:", (getattr(blocks[0], "text", "") or "")[:160].replace("\n", " "))
        import tkinter  # the per-session password window needs Tk inside the exe
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
        print("  password window (Tk): ok")
        return

    if a.proxy:
        return run_proxy()
    if a.pair:
        return setup_client(a.pair, write_configs=not a.no_claude_config)
    if a.uninstall:
        return uninstall()
    if a.server:
        return run_server(load_server())
    # double-click: an already-configured AutoCAD PC just starts sharing
    if not a.setup and read_json(ROLE_CFG).get("role") == "server":
        return run_server(load_server())
    menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        import traceback
        log = CFG_DIR / "error.log"
        try:
            CFG_DIR.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} v{VERSION} args={sys.argv[1:]}\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        print(f"\nERROR: {exc}\n(details saved to {log})", file=sys.stderr)
        pause()
        sys.exit(1)
