"""
Pro tools for AutoCAD Remote MCP.

Everything here uses AutoCAD's COM automation API directly. Nothing is typed into the
AutoCAD command line, no LISP, no scripts - so safe mode stays fully intact.
Imported at the end of acad_server.py so the tools register on the same MCP server.
"""

from __future__ import annotations

import io
import math
import os
import re

from acad_server import (
    ToolError, _acad, _autocad_windows, _by_handle, _command_target, _darr, _doc, _entity_info, _objs,
    _pt, _r3, _safe_path, _space, _style, _units, mcp, run_com,
)

# =============================================================================
# geometry helpers
# =============================================================================


def _xy(p):
    return (float(p[0]), float(p[1]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _mul(a, k):
    return (a[0] * k, a[1] * k)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _unit(v):
    n = math.hypot(*v)
    if n < 1e-12:
        raise ToolError("Zero-length direction.")
    return (v[0] / n, v[1] / n)


def _line(e):
    if e.ObjectName != "AcDbLine":
        raise ToolError(f"Entity {e.Handle} is a {e.ObjectName}; this tool works on LINE entities.")
    return _xy(e.StartPoint), _xy(e.EndPoint)


def _inter_inf(p1, p2, p3, p4):
    """Intersection of the infinite lines p1-p2 and p3-p4 (None if parallel)."""
    d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0])
    if abs(d) < 1e-12:
        return None
    a = p1[0] * p2[1] - p1[1] * p2[0]
    b = p3[0] * p4[1] - p3[1] * p4[0]
    return ((a * (p3[0] - p4[0]) - (p1[0] - p2[0]) * b) / d, (a * (p3[1] - p4[1]) - (p1[1] - p2[1]) * b) / d)


def _set_end(e, which, p):
    if which == "start":
        e.StartPoint = _pt(*p)
    else:
        e.EndPoint = _pt(*p)


def _corner_setup(l1, l2):
    """For two lines meeting at a corner: intersection, which end of each to move, unit directions away."""
    a1, a2 = _line(l1)
    b1, b2 = _line(l2)
    ip = _inter_inf(a1, a2, b1, b2)
    if ip is None:
        raise ToolError("The lines are parallel - they never meet.")
    out = []
    for s, e in ((a1, a2), (b1, b2)):
        which, far = ("start", e) if _dist(s, ip) < _dist(e, ip) else ("end", s)
        out.append((which, far, _unit(_sub(far, ip))))
    return ip, out


def _points_on(ent_a, ent_b, mode):
    """IntersectWith points (mode 0 = real crossings, 1 = extend ent_a)."""
    raw = ent_a.IntersectWith(ent_b, mode)
    flat = list(raw) if raw else []
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 2, 3)]


def _bbox(e):
    mn, mx = e.GetBoundingBox()
    return float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])


def _bbox_all(objs):
    boxes = [_bbox(o) for o in objs]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


# =============================================================================
# vision: screenshot
# =============================================================================


@mcp.tool()
async def capture_screenshot(zoom_extents: bool = False, area: str = "drawing", max_width: int = 1600):
    """Take a picture of the AutoCAD window so you can SEE the drawing (check layout, spacing, overlaps,
    labels). area: 'drawing' (drawing area only) or 'window' (whole AutoCAD window). zoom_extents=true first
    zooms to show everything. Returns a PNG image. Use it to verify your work after drawing."""
    from acad_server import Image  # mcp Image helper, re-exported by acad_server

    if zoom_extents:
        await run_com(lambda: _acad().ZoomExtents())
    max_width = max(400, min(int(max_width), 2400))

    def grab():
        import ctypes

        import win32gui
        import win32ui
        from PIL import Image as PILImage

        wins = _autocad_windows()
        if not wins:
            raise ToolError("AutoCAD window not found on the AutoCAD PC.")
        hwnd = wins[0]
        if win32gui.IsIconic(hwnd):
            raise ToolError("AutoCAD is minimized on the AutoCAD PC - restore its window to take a screenshot.")
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        try:
            ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
            info = bmp.GetInfo()
            img = PILImage.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True),
                                      "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)
        if area.lower().startswith("draw"):
            child = _command_target(hwnd)
            if child != hwnd:
                cl, ct, cr, cb = win32gui.GetWindowRect(child)
                img = img.crop((cl - l, ct - t, cr - l, cb - t))
        if img.width > max_width:
            img = img.resize((max_width, int(img.height * max_width / img.width)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    import asyncio
    png = await asyncio.get_running_loop().run_in_executor(None, grab)
    return Image(data=png, format="png")


# =============================================================================
# dimensions
# =============================================================================


def _dim_out(dm, layer):
    _style(dm, layer, None)
    return {"handle": dm.Handle, "type": dm.ObjectName, "measurement": round(float(dm.Measurement), 6)}


@mcp.tool()
async def add_dimension_linear(x1: float, y1: float, x2: float, y2: float, dim_x: float, dim_y: float,
                               direction: str = "auto", rotation_deg: float | None = None, layer: str = "") -> dict:
    """Linear dimension between (x1,y1) and (x2,y2) with the dimension line through (dim_x,dim_y).
    direction: 'horizontal', 'vertical' or 'auto' (by the larger difference). rotation_deg overrides the angle."""
    if rotation_deg is not None:
        ang = math.radians(rotation_deg)
    elif direction.lower().startswith("h"):
        ang = 0.0
    elif direction.lower().startswith("v"):
        ang = math.pi / 2
    else:
        ang = 0.0 if abs(x2 - x1) >= abs(y2 - y1) else math.pi / 2

    def f():
        return _dim_out(_space(_doc()).AddDimRotated(_pt(x1, y1), _pt(x2, y2), _pt(dim_x, dim_y), ang), layer)

    return await run_com(f)


@mcp.tool()
async def add_dimension_angular(cx: float, cy: float, x1: float, y1: float, x2: float, y2: float,
                                text_x: float, text_y: float, layer: str = "") -> dict:
    """Angular dimension: vertex (cx,cy), one point on each leg (x1,y1) and (x2,y2), arc/text through (text_x,text_y)."""

    def f():
        return _dim_out(_space(_doc()).AddDimAngular(_pt(cx, cy), _pt(x1, y1), _pt(x2, y2), _pt(text_x, text_y)), layer)

    return await run_com(f)


def _circle_like(e):
    if e.ObjectName not in ("AcDbCircle", "AcDbArc"):
        raise ToolError("Give a circle or an arc.")
    return _xy(e.Center), float(e.Radius)


@mcp.tool()
async def add_dimension_radius(handle: str, angle_deg: float = 45.0, leader_length: float = 0.0, layer: str = "") -> dict:
    """Radius dimension (R...) on a circle or arc, placed at angle_deg around it."""

    def f():
        d = _doc()
        c, r = _circle_like(_by_handle(d, handle))
        a = math.radians(angle_deg)
        p = (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
        return _dim_out(_space(d).AddDimRadial(_pt(*c), _pt(*p), float(leader_length)), layer)

    return await run_com(f)


@mcp.tool()
async def add_dimension_diameter(handle: str, angle_deg: float = 45.0, leader_length: float = 0.0, layer: str = "") -> dict:
    """Diameter dimension (Ø...) on a circle or arc, across the angle_deg direction."""

    def f():
        d = _doc()
        c, r = _circle_like(_by_handle(d, handle))
        a = math.radians(angle_deg)
        p1 = (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
        p2 = (c[0] - r * math.cos(a), c[1] - r * math.sin(a))
        return _dim_out(_space(d).AddDimDiametric(_pt(*p1), _pt(*p2), float(leader_length)), layer)

    return await run_com(f)


# =============================================================================
# modify: fillet, chamfer, trim, extend, break
# =============================================================================


@mcp.tool()
async def fillet_lines(handle1: str, handle2: str, radius: float = 0.0) -> dict:
    """Fillet two LINES: round the corner with an arc of `radius` (0 = sharp corner, lines just meet).
    The lines are trimmed/extended to the arc; the longer part of each line is kept."""
    if radius < 0:
        raise ToolError("radius must be 0 or more.")

    def f():
        d = _doc()
        l1, l2 = _by_handle(d, handle1), _by_handle(d, handle2)
        ip, ((w1, far1, u1), (w2, far2, u2)) = _corner_setup(l1, l2)
        theta = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
            raise ToolError("The lines are collinear - nothing to fillet.")
        if radius == 0:
            _set_end(l1, w1, ip)
            _set_end(l2, w2, ip)
            return {"corner": [round(ip[0], 6), round(ip[1], 6)], "arc": None}
        t = radius / math.tan(theta / 2)
        if t > _dist(ip, far1) or t > _dist(ip, far2):
            raise ToolError("Radius too large for these lines.")
        t1, t2 = _add(ip, _mul(u1, t)), _add(ip, _mul(u2, t))
        c = _add(ip, _mul(_unit(_add(u1, u2)), radius / math.sin(theta / 2)))
        a1, a2 = math.atan2(t1[1] - c[1], t1[0] - c[0]), math.atan2(t2[1] - c[1], t2[0] - c[0])
        if (a2 - a1) % (2 * math.pi) > math.pi:
            a1, a2 = a2, a1
        arc = _space(d).AddArc(_pt(*c), float(radius), a1, a2)
        arc.Layer = l1.Layer
        _set_end(l1, w1, t1)
        _set_end(l2, w2, t2)
        return {"arc": _entity_info(arc)}

    return await run_com(f)


@mcp.tool()
async def chamfer_lines(handle1: str, handle2: str, distance1: float, distance2: float | None = None) -> dict:
    """Chamfer two LINES: cut the corner with a straight line, distance1 along the first line and distance2
    (default = distance1) along the second, measured from the corner."""
    d2 = distance1 if distance2 is None else distance2

    def f():
        d = _doc()
        l1, l2 = _by_handle(d, handle1), _by_handle(d, handle2)
        ip, ((w1, far1, u1), (w2, far2, u2)) = _corner_setup(l1, l2)
        if distance1 > _dist(ip, far1) or d2 > _dist(ip, far2):
            raise ToolError("Chamfer distance too large for these lines.")
        t1, t2 = _add(ip, _mul(u1, distance1)), _add(ip, _mul(u2, d2))
        ch = _space(d).AddLine(_pt(*t1), _pt(*t2))
        ch.Layer = l1.Layer
        _set_end(l1, w1, t1)
        _set_end(l2, w2, t2)
        return {"chamfer_line": _entity_info(ch)}

    return await run_com(f)


@mcp.tool()
async def extend_line(handle: str, boundary_handle: str, end: str = "auto") -> dict:
    """Extend a LINE until it meets the boundary entity (line, arc, circle, polyline...).
    end: 'start', 'end' or 'auto' (whichever end needs the shortest extension)."""

    def f():
        d = _doc()
        ln, bd = _by_handle(d, handle), _by_handle(d, boundary_handle)
        s, e = _line(ln)
        v = _sub(e, s)
        L2 = v[0] ** 2 + v[1] ** 2
        best = None
        for p in _points_on(ln, bd, 1):
            t = ((p[0] - s[0]) * v[0] + (p[1] - s[1]) * v[1]) / L2
            if t > 1 + 1e-9 and end in ("auto", "end"):
                cand = ((t - 1), "end", p)
            elif t < -1e-9 and end in ("auto", "start"):
                cand = (-t, "start", p)
            else:
                continue
            if best is None or cand[0] < best[0]:
                best = cand
        if not best:
            raise ToolError("The line can't be extended to reach that boundary.")
        _set_end(ln, best[1], best[2])
        return _entity_info(ln)

    return await run_com(f)


@mcp.tool()
async def trim_line(handle: str, cutting_handles: list[str], pick_x: float, pick_y: float) -> dict:
    """Trim a LINE at its crossings with the cutting entities, removing the piece that contains the pick point
    (pick_x, pick_y) - like clicking the part to delete in AutoCAD's TRIM. A middle piece splits the line in two."""

    def f():
        d = _doc()
        ln = _by_handle(d, handle)
        s, e = _line(ln)
        v = _sub(e, s)
        L2 = v[0] ** 2 + v[1] ** 2
        ts = []
        for c in _objs(d, cutting_handles):
            for p in _points_on(ln, c, 0):
                t = ((p[0] - s[0]) * v[0] + (p[1] - s[1]) * v[1]) / L2
                if 1e-9 < t < 1 - 1e-9:
                    ts.append(t)
        if not ts:
            raise ToolError("The cutting entities don't cross this line.")
        cuts = [0.0] + sorted(set(round(t, 12) for t in ts)) + [1.0]
        tp = ((pick_x - s[0]) * v[0] + (pick_y - s[1]) * v[1]) / L2
        tp = min(max(tp, 0.0), 1.0)
        ta, tb = next((a, b) for a, b in zip(cuts, cuts[1:]) if a <= tp <= b)
        P = lambda t: (s[0] + v[0] * t, s[1] + v[1] * t)
        if ta == 0.0 and tb == 1.0:
            raise ToolError("Nothing to trim.")
        if ta == 0.0:
            ln.StartPoint = _pt(*P(tb))
            return {"kept": [_entity_info(ln)]}
        if tb == 1.0:
            ln.EndPoint = _pt(*P(ta))
            return {"kept": [_entity_info(ln)]}
        rest = ln.Copy()
        ln.EndPoint = _pt(*P(ta))
        rest.StartPoint = _pt(*P(tb))
        return {"kept": [_entity_info(ln), _entity_info(rest)]}

    return await run_com(f)


@mcp.tool()
async def break_line(handle: str, x: float, y: float, gap: float = 0.0) -> dict:
    """Split a LINE in two at the point nearest to (x,y). gap > 0 leaves an opening of that length."""

    def f():
        ln = _by_handle(_doc(), handle)
        s, e = _line(ln)
        v = _sub(e, s)
        L = math.hypot(*v)
        t = ((x - s[0]) * v[0] + (y - s[1]) * v[1]) / (L * L)
        h = (gap / 2) / L
        if not (h < t < 1 - h):
            raise ToolError("The break point must be inside the line (and the gap must fit).")
        rest = ln.Copy()
        ln.EndPoint = _pt(s[0] + v[0] * (t - h), s[1] + v[1] * (t - h))
        rest.StartPoint = _pt(s[0] + v[0] * (t + h), s[1] + v[1] * (t + h))
        return {"pieces": [_entity_info(ln), _entity_info(rest)]}

    return await run_com(f)


# =============================================================================
# properties & selection
# =============================================================================


@mcp.tool()
async def change_properties(handles: list[str], layer: str = "", color: int | None = None, linetype: str = "",
                            lineweight: int | None = None, linetype_scale: float | None = None) -> dict:
    """Change properties of many entities at once. lineweight in 1/100 mm (25 = 0.25 mm, -1 = ByLayer)."""

    def f():
        n = 0
        for o in _objs(_doc(), handles):
            _style(o, layer, color)
            if linetype:
                o.Linetype = linetype
            if lineweight is not None:
                o.Lineweight = int(lineweight)
            if linetype_scale is not None:
                o.LinetypeScale = float(linetype_scale)
            n += 1
        return {"changed": n}

    return await run_com(f)


@mcp.tool()
async def match_properties(source_handle: str, target_handles: list[str]) -> dict:
    """Copy layer, color, linetype, lineweight and linetype scale from one entity to others (like MATCHPROP)."""

    def f():
        d = _doc()
        s = _by_handle(d, source_handle)
        n = 0
        for o in _objs(d, target_handles):
            o.Layer, o.color, o.Linetype = s.Layer, s.color, s.Linetype
            try:
                o.Lineweight, o.LinetypeScale = s.Lineweight, s.LinetypeScale
            except Exception:
                pass
            n += 1
        return {"matched": n, "from": _entity_info(s)}

    return await run_com(f)


@mcp.tool()
async def select_entities(layer: str = "", entity_type: str = "", color: int | None = None,
                          window: list[float] | None = None, text_contains: str = "", limit: int = 500) -> dict:
    """Find entities by filter and return their handles (to pass to other tools). All filters are optional:
    layer, entity_type (e.g. AcDbLine, AcDbCircle, AcDbText), color index, window [x1,y1,x2,y2] (entities fully
    inside), text_contains (TEXT/MTEXT content)."""
    win = None
    if window:
        if len(window) != 4:
            raise ToolError("window must be [x1, y1, x2, y2].")
        win = (min(window[0], window[2]), min(window[1], window[3]), max(window[0], window[2]), max(window[1], window[3]))

    def f():
        out, total = [], 0
        for e in _doc().ModelSpace:
            if layer and e.Layer.lower() != layer.lower():
                continue
            if entity_type and e.ObjectName.lower() != entity_type.lower():
                continue
            if color is not None and e.color != color:
                continue
            if text_contains:
                try:
                    if text_contains.lower() not in e.TextString.lower():
                        continue
                except Exception:
                    continue
            if win:
                try:
                    b = _bbox(e)
                except Exception:
                    continue
                if not (b[0] >= win[0] and b[1] >= win[1] and b[2] <= win[2] and b[3] <= win[3]):
                    continue
            total += 1
            if len(out) < limit:
                out.append(e.Handle)
        return {"count": total, "handles": out}

    return await run_com(f)


@mcp.tool()
async def get_bounding_box(handles: list[str]) -> dict:
    """Combined bounding box (min/max corners, width, height, center) of the given entities."""

    def f():
        x1, y1, x2, y2 = _bbox_all(_objs(_doc(), handles))
        return {"min": [x1, y1], "max": [x2, y2], "width": x2 - x1, "height": y2 - y1,
                "center": [(x1 + x2) / 2, (y1 + y2) / 2]}

    return await run_com(f)


# =============================================================================
# text
# =============================================================================

TEXT_TYPES = ("AcDbText", "AcDbMText", "AcDbMLeader")


def _text_items(doc):
    for e in doc.ModelSpace:
        if e.ObjectName in TEXT_TYPES:
            yield e, e
        elif e.ObjectName == "AcDbBlockReference":
            try:
                if e.HasAttributes:
                    for a in e.GetAttributes():
                        yield a, e
            except Exception:
                pass


@mcp.tool()
async def find_text(text: str, case_sensitive: bool = False, limit: int = 200) -> dict:
    """Find TEXT, MTEXT, leader text and block attribute values containing `text`."""

    def f():
        needle = text if case_sensitive else text.lower()
        hits = []
        for obj, owner in _text_items(_doc()):
            s = obj.TextString
            if needle in (s if case_sensitive else s.lower()):
                hits.append({"handle": obj.Handle, "owner": owner.Handle, "type": obj.ObjectName, "text": s})
                if len(hits) >= limit:
                    break
        return {"count": len(hits), "matches": hits}

    return await run_com(f)


@mcp.tool()
async def replace_text(find: str, replace: str, case_sensitive: bool = False, handles: list[str] | None = None) -> dict:
    """Replace text in TEXT, MTEXT, leader text and block attributes (all of the drawing, or only `handles`)."""
    if not find:
        raise ToolError("find must not be empty.")
    flags = 0 if case_sensitive else re.IGNORECASE
    pat = re.compile(re.escape(find), flags)

    def f():
        d = _doc()
        only = {h.upper() for h in handles} if handles else None
        changed = 0
        for obj, owner in _text_items(d):
            if only and obj.Handle.upper() not in only and owner.Handle.upper() not in only:
                continue
            s = obj.TextString
            new, n = pat.subn(lambda _m: replace, s)
            if n:
                obj.TextString = new
                changed += 1
        return {"changed_objects": changed}

    return await run_com(f)


# =============================================================================
# blocks: attributes
# =============================================================================


@mcp.tool()
async def set_block_attributes(handle: str, values: dict[str, str]) -> dict:
    """Change attribute values of an inserted block, e.g. {"ROOM": "Kitchen", "AREA": "12.5"}."""

    def f():
        e = _by_handle(_doc(), handle)
        if e.ObjectName != "AcDbBlockReference" or not e.HasAttributes:
            raise ToolError("That entity is not a block with attributes.")
        vals = {k.upper(): str(v) for k, v in values.items()}
        done = []
        for a in e.GetAttributes():
            if a.TagString.upper() in vals:
                a.TextString = vals[a.TagString.upper()]
                done.append(a.TagString)
        return {"updated": done, "block": _entity_info(e)}

    return await run_com(f)


# =============================================================================
# styles & linetypes
# =============================================================================


@mcp.tool()
async def list_styles() -> dict:
    """List text styles (with fonts), dimension styles, loaded linetypes and the current styles."""

    def f():
        d = _doc()
        ts = []
        for s in d.TextStyles:
            try:
                font = s.fontFile or ""
                if not font:
                    font = s.GetFont()[0]
            except Exception:
                font = ""
            ts.append({"name": s.Name, "font": font, "height": s.Height})
        return {"text_styles": ts, "dimension_styles": [s.Name for s in d.DimStyles],
                "linetypes": [l.Name for l in d.Linetypes],
                "current_text_style": d.ActiveTextStyle.Name, "current_dimension_style": d.ActiveDimStyle.Name}

    return await run_com(f)


@mcp.tool()
async def create_text_style(name: str, font: str = "Arial", height: float = 0.0, width_factor: float = 1.0,
                            make_current: bool = True) -> dict:
    """Create (or update) a text style. font: an installed TrueType font name ('Arial', 'Calibri'...) or an
    AutoCAD .shx font name ('romans.shx'). height 0 = set height per text."""
    if any(ch in font for ch in "\\/:"):
        raise ToolError("Give a font name only, not a path.")

    def f():
        d = _doc()
        st = d.TextStyles.Add(name)
        if font.lower().endswith(".shx"):
            st.fontFile = font
        else:
            st.SetFont(font, False, False, 0, 34)
        st.Height = float(height)
        st.Width = float(width_factor)
        if make_current:
            d.ActiveTextStyle = st
        return {"name": st.Name, "font": font, "current": make_current}

    return await run_com(f)


@mcp.tool()
async def create_dimension_style(name: str, text_height: float = 2.5, arrow_size: float = 2.5, decimals: int = 2,
                                 overall_scale: float = 1.0, text_style: str = "", make_current: bool = True) -> dict:
    """Create a dimension style. overall_scale (DIMSCALE) multiplies text/arrow sizes, e.g. 100 for a 1:100
    drawing in mm."""

    def f():
        d = _doc()
        names = ("DIMTXT", "DIMASZ", "DIMDEC", "DIMSCALE", "DIMTXSTY")
        old = {}
        for n in names:
            try:
                old[n] = d.GetVariable(n)
            except Exception:
                pass
        d.SetVariable("DIMTXT", float(text_height))
        d.SetVariable("DIMASZ", float(arrow_size))
        d.SetVariable("DIMDEC", int(decimals))
        d.SetVariable("DIMSCALE", float(overall_scale))
        if text_style:
            d.TextStyles.Item(text_style)  # must exist
            d.SetVariable("DIMTXSTY", text_style)
        ds = d.DimStyles.Add(name)
        ds.CopyFrom(d)
        if make_current:
            d.ActiveDimStyle = ds
        else:
            for n, v in old.items():
                try:
                    d.SetVariable(n, v)
                except Exception:
                    pass
        return {"name": ds.Name, "current": make_current}

    return await run_com(f)


@mcp.tool()
async def set_current_style(kind: str, name: str) -> dict:
    """Make a style current. kind: 'text' or 'dimension'."""

    def f():
        d = _doc()
        if kind.lower().startswith("t"):
            d.ActiveTextStyle = d.TextStyles.Item(name)
        elif kind.lower().startswith("d"):
            d.ActiveDimStyle = d.DimStyles.Item(name)
        else:
            raise ToolError("kind must be 'text' or 'dimension'.")
        return {"kind": kind, "current": name}

    return await run_com(f)


@mcp.tool()
async def load_linetype(name: str) -> dict:
    """Load a standard linetype (DASHED, HIDDEN, CENTER, PHANTOM, DOT, DASHDOT, BORDER, DIVIDE, ACAD_ISO02W100...)
    from AutoCAD's own linetype files."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ToolError("Invalid linetype name.")

    def f():
        d = _doc()
        try:
            d.Linetypes.Item(name)
            return {"name": name, "loaded": True, "note": "already loaded"}
        except Exception:
            pass
        for lin in ("acadiso.lin", "acad.lin"):
            try:
                d.Linetypes.Load(name, lin)
                return {"name": name, "loaded": True, "file": lin}
            except Exception:
                continue
        raise ToolError(f"Linetype '{name}' not found in acadiso.lin / acad.lin.")

    return await run_com(f)


# =============================================================================
# layers
# =============================================================================


@mcp.tool()
async def rename_layer(old_name: str, new_name: str) -> dict:
    """Rename a layer (not layer 0 or Defpoints)."""

    def f():
        lay = _doc().Layers.Item(old_name)
        lay.Name = new_name
        return {"renamed": old_name, "to": lay.Name}

    return await run_com(f)


@mcp.tool()
async def delete_layer(name: str) -> dict:
    """Delete an EMPTY layer (one with no objects, not current, not 0/Defpoints)."""

    def f():
        _doc().Layers.Item(name).Delete()
        return {"deleted": name}

    return await run_com(f)


# =============================================================================
# area labels
# =============================================================================


def _centroid(e):
    if e.ObjectName == "AcDbPolyline":
        c = list(e.Coordinates)
        pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
        a = cx = cy = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            k = x1 * y2 - x2 * y1
            a += k
            cx += (x1 + x2) * k
            cy += (y1 + y2) * k
        if abs(a) > 1e-12:
            return cx / (3 * a), cy / (3 * a)
    if e.ObjectName in ("AcDbCircle", "AcDbEllipse"):
        return _xy(e.Center)
    b = _bbox(e)
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


@mcp.tool()
async def label_areas(handles: list[str], units: str = "m2", decimals: int = 2, text_height: float | None = None,
                      prefix: str = "", layer: str = "") -> dict:
    """Write the area inside each closed object (rooms) as a centered label, e.g. '12.50 m²'.
    units: 'm2', 'ft2' or 'drawing' (drawing units squared). prefix: text before the number, e.g. 'Area: '."""

    def f():
        d = _doc()
        unit, fac = _units(d)
        out = []
        for o in _objs(d, handles):
            try:
                a = float(o.Area)
            except Exception:
                out.append({"handle": o.Handle, "error": "no area"})
                continue
            if units == "m2" and fac:
                val, suffix = a * fac * fac, " m\\U+00B2"
            elif units == "ft2" and fac:
                val, suffix = a * fac * fac / 0.09290304, " ft\\U+00B2"
            else:
                val, suffix = a, f" {unit}\\U+00B2" if unit != "unitless" else ""
            c = _centroid(o)
            h = text_height or max(math.sqrt(max(a, 1e-9)) / 12, 1e-6)
            mt = _space(d).AddMText(_pt(*c), 0, f"{prefix}{val:.{int(decimals)}f}{suffix}")
            mt.Height = h
            mt.AttachmentPoint = 5  # middle center
            mt.InsertionPoint = _pt(*c)
            _style(mt, layer, None)
            out.append({"handle": o.Handle, "label": mt.Handle, "value": round(val, int(decimals)),
                        "at": [round(c[0], 3), round(c[1], 3)]})
        return {"labels": out}

    return await run_com(f)


# =============================================================================
# view
# =============================================================================


@mcp.tool()
async def zoom_window(x1: float, y1: float, x2: float, y2: float) -> dict:
    """Zoom the view to the rectangle (x1,y1)-(x2,y2)."""

    def f():
        _acad().ZoomWindow(_pt(x1, y1), _pt(x2, y2))
        return {"ok": True}

    return await run_com(f)


@mcp.tool()
async def zoom_to_entities(handles: list[str], margin: float = 0.1) -> dict:
    """Zoom the view to fit the given entities, with a margin (0.1 = 10%)."""

    def f():
        x1, y1, x2, y2 = _bbox_all(_objs(_doc(), handles))
        mx, my = (x2 - x1) * margin + 1e-6, (y2 - y1) * margin + 1e-6
        _acad().ZoomWindow(_pt(x1 - mx, y1 - my), _pt(x2 + mx, y2 + my))
        return {"ok": True}

    return await run_com(f)


VIEWS = {"top": (0, 0, 1), "bottom": (0, 0, -1), "front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0),
         "right": (1, 0, 0), "iso_sw": (-1, -1, 1), "iso_se": (1, -1, 1), "iso_ne": (1, 1, 1), "iso_nw": (-1, 1, 1)}


@mcp.tool()
async def set_view(preset: str = "top") -> dict:
    """Set the model view direction: top, bottom, front, back, left, right, iso_sw, iso_se, iso_ne, iso_nw.
    Use an iso view to look at 3D solids, 'top' to go back to the normal plan view."""
    key = preset.strip().lower()
    if key not in VIEWS:
        raise ToolError(f"preset must be one of {', '.join(VIEWS)}")

    def f():
        d = _doc()
        vp = d.ActiveViewport
        vp.Direction = _pt(*VIEWS[key])
        d.ActiveViewport = vp
        _acad().ZoomExtents()
        return {"view": key}

    return await run_com(f)


# =============================================================================
# layouts & viewports
# =============================================================================


@mcp.tool()
async def list_layouts() -> dict:
    """List layouts (sheets) in tab order and the current one."""

    def f():
        d = _doc()
        lays = sorted(((l.TabOrder, l.Name) for l in d.Layouts))
        return {"layouts": [n for _, n in lays], "current": d.ActiveLayout.Name}

    return await run_com(f)


SHEETS = {"A4": "A4", "A3": "A3", "A2": "A2", "A1": "A1", "A0": "A0", "LETTER": "ANSI_A", "TABLOID": "ANSI_B"}


def _pick_media(lay, paper: str, landscape: bool):
    key = SHEETS.get(paper.strip().upper())
    if not key:
        raise ToolError(f"paper must be one of {', '.join(SHEETS)}")
    family, size = ("ANSI", key[5:]) if key.startswith("ANSI_") else ("ISO", key)
    name_re = re.compile(rf"^{family}_(FULL_BLEED_)?{size}_\(")
    best = None
    for m in lay.GetCanonicalMediaNames():
        mu = m.upper()
        dims = re.search(r"\((\d+(?:\.\d+)?)_X_(\d+(?:\.\d+)?)_", mu)
        if not name_re.match(mu) or not dims:
            continue
        if (float(dims.group(1)) > float(dims.group(2))) != landscape:
            continue
        score = 0 if "FULL_BLEED" in mu else 1
        if best is None or score < best[0]:
            best = (score, m)
    if not best:
        raise ToolError(f"Paper {paper} not available.")
    return best[1]


@mcp.tool()
async def create_layout(name: str, paper: str = "A3", landscape: bool = True) -> dict:
    """Create a new layout (sheet) ready for PDF: paper A4/A3/A2/A1/A0/LETTER/TABLOID, sheet units in mm
    (A3 landscape = 420 x 297). Then add viewports with create_viewport and export with export_pdf(layout=...)."""

    def f():
        d = _doc()
        lay = d.Layouts.Add(name)
        d.ActiveLayout = lay
        lay.RefreshPlotDeviceInfo()
        lay.ConfigName = "DWG To PDF.pc3"
        lay.RefreshPlotDeviceInfo()
        lay.CanonicalMediaName = _pick_media(lay, paper, landscape)
        lay.PaperUnits = 1  # millimetres
        lay.PlotRotation = 0
        w, h = lay.GetPaperSize()
        return {"layout": lay.Name, "paper": lay.CanonicalMediaName, "sheet_size_mm": [w, h],
                "hint": f"Sheet center is ({w / 2}, {h / 2}). Use create_viewport with mm coordinates."}

    return await run_com(f)


@mcp.tool()
async def set_current_layout(name: str) -> dict:
    """Switch to a layout tab, or 'Model' for model space."""

    def f():
        d = _doc()
        d.ActiveLayout = d.Layouts.Item(name)
        return {"current": d.ActiveLayout.Name}

    return await run_com(f)


@mcp.tool()
async def create_viewport(layout: str, center_x: float, center_y: float, width: float, height: float,
                          scale: float | None = None, target_x: float | None = None,
                          target_y: float | None = None) -> dict:
    """Add a viewport on a layout (paper units, e.g. mm on an A3 sheet) that shows the model, centered on the
    whole drawing. scale (optional) sets the plot scale, e.g. 0.01 for 1:100; default = fit the drawing.
    target_x/target_y: optional model point to center on instead."""

    def f():
        from acad_server import _model_extents
        d = _doc()
        ext = _model_extents(d)
        if not ext and (target_x is None or target_y is None or not scale):
            raise ToolError("The model is empty - draw something first, or give target_x, target_y and scale.")
        ex1, ey1, ex2, ey2 = ext or (0.0, 0.0, 1.0, 1.0)
        d.ActiveLayout = d.Layouts.Item(layout)
        vp = d.PaperSpace.AddPViewport(_pt(center_x, center_y), float(width), float(height))
        vp.Display(True)
        vp.Direction = _pt(0, 0, 1)
        tx = target_x if target_x is not None else (ex1 + ex2) / 2
        ty = target_y if target_y is not None else (ey1 + ey2) / 2
        vp.Target = _pt(tx, ty)
        s = float(scale) if scale else 0.95 * min(width / max(ex2 - ex1, 1e-9), height / max(ey2 - ey1, 1e-9))
        vp.CustomScale = s
        d.Regen(1)
        return {"viewport": vp.Handle, "layout": layout, "scale": round(s, 8),
                "scale_ratio": f"1:{round(1 / s, 2)}" if s else None, "centered_on": [round(tx, 3), round(ty, 3)]}

    return await run_com(f)


# =============================================================================
# external references (drawings folder only)
# =============================================================================


@mcp.tool()
async def attach_xref(file_name: str, x: float = 0.0, y: float = 0.0, scale: float = 1.0, overlay: bool = False,
                      name: str = "") -> dict:
    """Attach another .dwg from the drawings folder as an external reference (xref)."""
    path = _safe_path(file_name, must_exist=True, exts={".dwg"})
    xname = name or os.path.splitext(os.path.basename(path))[0]

    def f():
        ref = _space(_doc()).AttachExternalReference(path, xname, _pt(x, y), scale, scale, scale, 0.0, bool(overlay))
        return {"handle": ref.Handle, "name": xname, "path": path}

    return await run_com(f)


@mcp.tool()
async def list_xrefs() -> dict:
    """List external references attached to the drawing."""

    def f():
        out = []
        for b in _doc().Blocks:
            try:
                if b.IsXRef:
                    out.append({"name": b.Name, "path": b.Path})
            except Exception:
                pass
        return {"xrefs": out}

    return await run_com(f)


@mcp.tool()
async def detach_xref(name: str) -> dict:
    """Detach (remove) an external reference by name."""

    def f():
        b = _doc().Blocks.Item(name)
        if not b.IsXRef:
            raise ToolError(f"'{name}' is not an xref.")
        b.Detach()
        return {"detached": name}

    return await run_com(f)


# =============================================================================
# 3D solids
# =============================================================================


@mcp.tool()
async def create_solid(shape: str, x: float, y: float, z: float = 0.0, length: float = 10.0, width: float = 10.0,
                       height: float = 10.0, radius: float = 5.0, layer: str = "", color: int | None = None) -> dict:
    """Create a 3D solid centered at (x,y,z). shape: box (length,width,height), cylinder (radius,height),
    sphere (radius), cone (radius,height), wedge (length,width,height). Use set_view('iso_se') to look at it."""
    s = shape.strip().lower()

    def f():
        sp = _space(_doc())
        c = _pt(x, y, z)
        if s == "box":
            e = sp.AddBox(c, length, width, height)
        elif s == "cylinder":
            e = sp.AddCylinder(c, radius, height)
        elif s == "sphere":
            e = sp.AddSphere(c, radius)
        elif s == "cone":
            e = sp.AddCone(c, radius, height)
        elif s == "wedge":
            e = sp.AddWedge(c, length, width, height)
        else:
            raise ToolError("shape must be box, cylinder, sphere, cone or wedge.")
        _style(e, layer, color)
        return {"handle": e.Handle, "type": e.ObjectName, "volume": round(float(e.Volume), 6)}

    return await run_com(f)


@mcp.tool()
async def extrude(handle: str, height: float, taper_deg: float = 0.0, layer: str = "") -> dict:
    """Extrude a closed 2D shape (closed polyline, circle, ellipse, closed spline) into a 3D solid."""

    def f():
        d = _doc()
        prof = _by_handle(d, handle)
        from acad_server import _disp_array
        regions = d.ModelSpace.AddRegion(_disp_array([prof]))
        reg = regions[0] if isinstance(regions, (tuple, list)) else regions
        try:
            sol = d.ModelSpace.AddExtrudedSolid(reg, float(height), math.radians(taper_deg))
        finally:
            reg.Delete()
        _style(sol, layer, None)
        return {"handle": sol.Handle, "type": sol.ObjectName, "volume": round(float(sol.Volume), 6)}

    return await run_com(f)


@mcp.tool()
async def solid_boolean(handle_a: str, handle_b: str, operation: str) -> dict:
    """Combine two 3D solids: operation 'union', 'subtract' (A minus B) or 'intersect'. Solid B is consumed."""
    ops = {"union": 0, "intersect": 1, "subtract": 2}
    op = ops.get(operation.strip().lower())
    if op is None:
        raise ToolError("operation must be union, subtract or intersect.")

    def f():
        d = _doc()
        a, b = _by_handle(d, handle_a), _by_handle(d, handle_b)
        a.Boolean(op, b)
        return {"handle": a.Handle, "volume": round(float(a.Volume), 6)}

    return await run_com(f)
