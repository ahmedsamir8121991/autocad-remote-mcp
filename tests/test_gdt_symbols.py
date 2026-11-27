"""GD&T symbols in live AutoCAD: frames must show real symbols (gdt.shx), not '?'.
Opens a NEW drawing. Run: .venv\\Scripts\\python.exe tests\\test_gdt_symbols.py"""

import asyncio
import base64
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import mcp.types as T  # noqa: E402

import acad_server  # noqa: E402

acad_server.build_app("x" * 64, folders_provider=lambda: [tempfile.gettempdir()], discovery="full")
m = acad_server.mcp


def parts(r):
    r = r[0] if isinstance(r, tuple) else r
    return r.content if isinstance(r, T.CallToolResult) else r


def text(r):
    return " ".join(getattr(b, "text", "") for b in parts(r))


async def main():
    await m.call_tool("new_drawing", {})
    frames = [
        {"symbol": "flatness", "tolerance": 0.05, "x": 0, "y": 60},
        {"symbol": "position", "tolerance": 0.1, "x": 0, "y": 45, "diameter": True, "modifier": "M",
         "datums": ["A", "B"]},
        {"symbol": "perpendicularity", "tolerance": 0.02, "x": 0, "y": 30, "datums": ["A"]},
        {"symbol": "total_runout", "tolerance": 0.03, "x": 0, "y": 15, "datums": ["A"]},
        {"symbol": "circularity", "tolerance": 0.01, "x": 0, "y": 0},
    ]
    handles = []
    for f in frames:
        r = json.loads(text(await m.call_tool("gd_frame", f)))
        handles += r["handles"]
        print(f"{f['symbol']:18} ok={r['ok']} compartments={r['compartments']}")
    await m.call_tool("datum_feature", {"letter": "A", "x": 80, "y": 20})
    # read back the symbol texts: MTEXT with the GDT font code, no Unicode glyph left
    mtexts = []
    for h in handles:
        info = json.loads(text(await m.call_tool("get_entity", {"handle": h})))
        if "MText" in info.get("type", ""):
            mtexts.append(info.get("text") or info.get("contents") or "")
    print("MTEXT symbol cells:", len(mtexts), mtexts[:6])
    await m.call_tool("zoom_window", {"x1": -5, "y1": -8, "x2": 105, "y2": 70})
    shot = parts(await m.call_tool("capture_screenshot", {}))
    img = [b for b in shot if getattr(b, "type", "") == "image"]
    if img:
        with open(os.path.join(HERE, "gdt_symbols.png"), "wb") as fh:
            fh.write(base64.b64decode(img[0].data))
        print("screenshot saved")


asyncio.run(main())
