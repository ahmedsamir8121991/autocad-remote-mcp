"""Quick wins on live AutoCAD: toleranced dimensions, one AutoCAD link, lasting history, template check.
Opens NEW drawings only. Run: .venv\\Scripts\\python.exe tests\\test_quickwins.py"""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="acad_qw_local_")  # isolated history + engine state

import mcp.types as T  # noqa: E402

FOLDER = tempfile.mkdtemp(prefix="acad_qw_dwg_")
shutil.copy(r"C:\autocad-mcp-pro\templates\iso_a3_mech.dwt", os.path.join(FOLDER, "company.dwt"))

import acad_server  # noqa: E402

acad_server.build_app("x" * 64, folders_provider=lambda: [FOLDER], discovery="full")
m = acad_server.mcp
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {str(detail)[:400]}" if detail and not ok else ""),
          flush=True)


def parts(r):
    r = r[0] if isinstance(r, tuple) else r
    return r.content if isinstance(r, T.CallToolResult) else r


def text(r):
    return " ".join(getattr(b, "text", "") for b in parts(r))


async def call(name, args=None):
    try:
        return text(await m.call_tool(name, args or {}))
    except Exception as e:
        return f"EXC {type(e).__name__}: {e}"


def js(s):
    try:
        return json.loads(s)
    except ValueError:
        return {}


async def main():
    # ---- toleranced dimensions
    await call("new_drawing")
    a_name = js(await call("drawing_info")).get("name")
    await call("draw_line", {"x1": 0, "y1": 0, "x2": 20, "y2": 0})
    await call("draw_circle", {"cx": 50, "cy": 0, "radius": 6})
    r = js(await call("dimension_linear", {"x1": 0, "y1": 0, "x2": 20, "y2": 0, "dim_x": 10, "dim_y": -8,
                                           "fit": "H7"}))
    check("Linear dimension with ISO 286 fit H7 (text: 20 +0.021/0 H7 - see quickwins_dims.png)",
          r.get("type") == "ROTATEDDIMENSION" and bool(r.get("handle")), r)
    r = js(await call("dimension_diameter", {"x1": 44, "y1": 0, "x2": 56, "y2": 0, "leader_length": 8,
                                             "tol_upper": 0.02, "tol_lower": 0.01, "tol_mode": "deviation"}))
    check("Diameter dimension with +0.02/-0.01 deviation", bool(r) and "error" not in json.dumps(r).lower(), r)
    await call("zoom_extents")
    shot = [b for b in parts(await m.call_tool("capture_screenshot", {})) if getattr(b, "type", "") == "image"]
    if shot:
        with open(os.path.join(HERE, "quickwins_dims.png"), "wb") as fh:
            fh.write(base64.b64decode(shot[0].data))

    # ---- one AutoCAD link: the engine follows the drawing the app chose
    await call("new_drawing")
    b_name = js(await call("drawing_info")).get("name")
    r = await call("entity_create_point", {"x": 1, "y": 1})
    check(f"Engine works in the drawing the app just opened ({b_name})", "EXC" not in r and
          "active_document_changed" not in r, r)
    await call("switch_drawing", {"name": a_name})
    r = await call("entity_create_point", {"x": 2, "y": 2})
    info = js(await call("drawing_info"))
    pts = info.get("modelspace_entity_counts", {}).get("AcDbPoint", 0)
    check(f"After switch_drawing back to {a_name}, the engine draws there too",
          info.get("name") == a_name and pts == 1 and "active_document_changed" not in r, (r, info))

    # ---- lasting history
    cp = js(await call("checkpoint_create", {"label": "before-cleanup"}))
    check("Checkpoint saves a lasting snapshot too", isinstance(cp.get("saved"), dict) and
          "error" not in json.dumps(cp.get("saved")), cp)
    # (the history file is written by the HTTP layer; e2e_password.py checks it through the real path.
    #  Here: entries written now are reloaded by a new process.)
    import acad_skills
    acad_skills.journal_add({"tool": "quickwins_probe", "args": {}, "ok": True, "result": "probe"})
    probe = ("import sys, json; sys.path.insert(0, r'%s'); import acad_skills as K; "
             "print('LOADED', len(K.JOURNAL), json.dumps([r.get('tool') for r in K.JOURNAL][-5:]))" % SRC)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         env=dict(os.environ), timeout=180).stdout
    check("A new process (restart) reloads the history", "quickwins_probe" in out, out[-300:])
    r = js(await call("checkpoint_restore", {"label": "before-cleanup", "from_saved": True}))
    check("Saved checkpoint reopens as a new drawing", "saved snapshot" in json.dumps(r) and
          "error" not in json.dumps(r).lower(), r)

    # ---- template check
    await call("new_drawing")
    await call("create_layer", {"name": "MyOwnLayer", "color": 3})
    await call("draw_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 10, "layer": "MyOwnLayer"})
    await call("create_layer", {"name": "TEXT", "color": 1})  # the template's TEXT layer has another colour
    rep = js(await call("template_check", {"template": "company.dwt"}))
    check("Template check runs and reports not compliant", rep.get("compliant") is False, rep)
    check("Entities on a layer the template doesn't have are reported",
          any(x.get("layer") == "MyOwnLayer" for x in rep.get("nonstandard_layers_in_use", [])), rep)
    check("A template layer with a different colour is reported",
          any(d.get("layer", "").upper() == "TEXT" and d.get("property") == "color"
              for d in rep.get("layers", {}).get("different_properties", [])), rep.get("layers"))
    out = await call("template_check", {"template": r"C:\Windows\win.ini"})
    check("Template outside the drawings folder is refused", "EXC" in out or "only" in out.lower(), out)

    print(f"\n{sum(results)}/{len(results)} passed")


asyncio.run(main())
