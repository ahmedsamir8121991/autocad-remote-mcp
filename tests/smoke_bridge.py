"""Smoke test: app tools on mcp 1.x + bridged AutoCAD MCP Pro tools, in one process, on live AutoCAD.
Run with the engine's venv:  C:\\autocad-mcp-pro\\.venv\\Scripts\\python.exe tests\\smoke_bridge.py
Opens a NEW drawing; does not touch other open drawings."""

import asyncio
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

folder = os.path.join(tempfile.gettempdir(), "acad_bridge_smoke")
os.makedirs(folder, exist_ok=True)

import acad_server  # noqa: E402

acad_server.build_app("x" * 64, folders_provider=lambda: [folder])
mcp = acad_server.mcp


def short(r):
    if isinstance(r, tuple):
        r = r[0]
    if hasattr(r, "content"):
        err = " ERROR" if r.isError else ""
        r = r.content
    else:
        err = ""
    t = " | ".join(getattr(b, "text", f"<{getattr(b, 'type', '?')}>") for b in r)
    return (t[:300] + err)


async def main():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    print("total tools:", len(names))
    for must in ("draw_line", "gd_frame", "layer_isolate", "sheet_frame"):
        print(f"  has {must}:", must in names)
    for bad in ("system_run_lisp", "system_run_command", "system_launch"):
        print(f"  blocked {bad}:", bad not in names)
    steps = [
        ("new_drawing", {}),
        ("draw_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 50}),
        ("layer_state_list", {}),
        ("entity_create_revcloud", {"points": [[10, 10], [40, 10], [40, 30], [10, 30]], "segment_length": 5}),
        ("gd_frame", {"x": 120, "y": 20, "symbol": "flatness", "tolerance": 0.05}),
        ("drawing_export_dxf", {"path": "C:\\Windows\\Temp\\evil.dxf"}),
        ("drawing_export_dxf", {"path": os.path.join(os.path.expanduser("~"), "Desktop", "outside.dxf")}),
        ("drawing_export_dxf", {"path": os.path.join(folder, "smoke.dxf")}),
        ("drawing_info", {}),
    ]
    for name, args in steps:
        try:
            r = await mcp.call_tool(name, args)
            print(f"{name}: {short(r)}")
        except Exception as e:
            print(f"{name}: EXC {type(e).__name__}: {e}"[:400])
    print("smoke.dxf exists:", os.path.exists(os.path.join(folder, "smoke.dxf")))


asyncio.run(main())
