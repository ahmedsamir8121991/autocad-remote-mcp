"""Tool search for AutoCAD Remote MCP: search_tools, describe_tool, call_tool.

With 240+ tools, sending every schema to Claude up front costs tokens and makes it pick worse.
In "search" mode (the default) Claude is shown only a short pinned list plus these three tools;
it searches the catalogue the way a drafter thinks - by AutoCAD command (BPOLY, QSELECT, FILLET)
or plain words ("round off a corner", "parts list", "title block") - then reads one tool's full
schema and calls it. Every tool stays callable directly by its name as well.

Search vocabulary: tool name, description and parameter names, plus the AutoCAD command /
synonym corpus of the AutoCAD MCP Pro engine (discovery/aliases.py, MIT). The app's own tools
borrow the vocabulary of their engine twin (draw_line -> entity_create_line) and some extras.
"""

import json
import math
import re
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

# Shown to Claude in search mode (besides search_tools / describe_tool / call_tool).
PINNED = (
    "autocad_status", "drawing_info", "capture_screenshot", "list_entities", "list_layers",
    "cad_batch", "recipes", "zoom_extents", "cancel_command",
)
DISCOVERY_TOOLS = ("search_tools", "describe_tool", "call_tool")

# The app's own tools -> the engine tool(s) whose search vocabulary they share.
APP_TWINS: dict[str, tuple[str, ...]] = {
    "add_dimension": ("dimension_aligned",), "add_dimension_angular": ("dimension_angular",),
    "add_dimension_diameter": ("dimension_diameter",), "add_dimension_linear": ("dimension_linear",),
    "add_dimension_radius": ("dimension_radius",), "add_hatch": ("entity_create_hatch",),
    "add_leader": ("leader_create_mleader",), "add_table": ("entity_create_table",),
    "add_text": ("entity_create_text", "entity_create_mtext"), "array_polar": ("entity_array_polar",),
    "array_rectangular": ("entity_array_rectangular",), "attach_xref": ("xref_attach",),
    "autocad_status": ("system_status",), "capture_screenshot": ("view_screenshot",),
    "chamfer_lines": ("entity_chamfer",), "change_properties": ("entity_set_properties",),
    "create_block": ("block_define",), "create_dimension_style": ("dimstyle_create",),
    "create_layer": ("layer_create",), "create_layout": ("layout_create",),
    "create_text_style": ("textstyle_create",), "create_viewport": ("viewport_create",),
    "delete_entities": ("entity_delete",), "delete_layer": ("layer_delete",), "detach_xref": ("xref_manage",),
    "draw_arc": ("entity_create_arc",), "draw_circle": ("entity_create_circle",),
    "draw_ellipse": ("entity_create_ellipse",), "draw_line": ("entity_create_line",),
    "draw_polyline": ("entity_create_polyline",), "draw_rectangle": ("entity_create_rectangle",),
    "draw_spline": ("entity_create_spline",), "explode_entity": ("block_explode",),
    "export_pdf": ("drawing_export_pdf",), "extend_line": ("entity_extend",), "fillet_lines": ("entity_fillet",),
    "find_text": ("text_find_replace",), "get_bounding_box": ("analysis_bounding_box",),
    "get_entity": ("entity_get",), "get_variable": ("system_get_variable",), "insert_block": ("block_insert",),
    "list_blocks": ("block_list",), "list_drawings": ("document_list",), "list_entities": ("entity_list",),
    "list_layers": ("layer_list",), "list_layouts": ("layout_list",),
    "list_styles": ("textstyle_list", "dimstyle_list"), "list_xrefs": ("xref_manage",),
    "load_linetype": ("linetype_load",), "measure_area": ("analysis_measure_area",),
    "measure_distance": ("analysis_measure_distance",), "measure_polygon": ("analysis_measure_area",),
    "modify_entity": ("entity_edit_geometry",), "new_drawing": ("drawing_new",), "offset_entity": ("entity_offset",),
    "open_drawing": ("drawing_open",), "rename_layer": ("layer_rename",), "replace_text": ("text_find_replace",),
    "save_drawing": ("drawing_save", "drawing_save_as"), "select_entities": ("selection_filter",),
    "set_block_attributes": ("block_set_attributes",), "set_current_layer": ("layer_set_current",),
    "set_current_layout": ("layout_set_current",),
    "set_current_style": ("textstyle_set_current", "dimstyle_set_current"),
    "set_layer_state": ("layer_freeze", "layer_thaw", "layer_lock", "layer_unlock", "layer_modify"),
    "set_variable": ("system_set_variable",), "switch_drawing": ("document_activate",),
    "transform_entity": ("entity_move", "entity_copy", "entity_rotate", "entity_scale"),
    "trim_line": ("entity_trim",), "zoom_extents": ("view_zoom_extents",), "zoom_window": ("view_zoom_window",),
    "mirror_entities": ("entity_mirror",),
}

# Vocabulary for app tools with no engine twin: (AutoCAD commands, phrases).
APP_EXTRA: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "break_line": (("BREAK", "BREAKATPOINT"), ("split a line", "cut a gap", "break at point")),
    "cancel_command": (("ESC", "CANCEL"), ("stuck prompt", "press escape", "autocad busy", "unfreeze")),
    "create_solid": (("BOX", "CYLINDER", "SPHERE", "CONE", "WEDGE"), ("3d solid", "solid box", "3d shape")),
    "extrude": (("EXTRUDE",), ("3d from profile", "pull up a shape", "thickness")),
    "solid_boolean": (("UNION", "SUBTRACT", "INTERSECT"), ("combine solids", "cut a solid", "boolean")),
    "set_view": (("VIEW", "VPOINT", "-VIEW", "PLAN"), ("isometric", "top view", "front view", "3d view")),
    "zoom_to_entities": (("ZOOM",), ("zoom to object", "zoom to selection", "show these entities")),
    "match_properties": (("MATCHPROP", "MA"), ("copy properties", "format painter", "same style as")),
    "run_command": (("REGEN", "UNDO", "REDO", "AUDIT", "PURGE"), ("regenerate", "clean up drawing", "repair")),
    "list_drawing_files": ((), ("drawings folder", "files I can open", "list dwg files")),
    "label_areas": ((), ("room areas", "write area in room", "m2 labels", "area text")),
    "quantity_takeoff": ((), ("bill of quantities", "lengths per layer", "count blocks", "boq")),
    "recipes": ((), ("how to", "step by step", "workflow", "guide")),
    "system_capabilities": ((), ("what can you do", "available features", "limits")),
    "template_check": (("STANDARDS", "CHECKSTANDARDS"),
                       ("company template", "cad standards", "standards check", "compare to template",
                        "check against template", "dwt", "layer standards")),
    "checkpoint_create": ((), ("restore point", "save a version", "backup before changes", "snapshot")),
    "checkpoint_restore": ((), ("go back", "restore version", "undo to checkpoint", "recover drawing")),
    "drawing_history": ((), ("what changed", "activity log", "history", "who did what", "previous value")),
    # toleranced dimensions (engine) - make sure 'tolerance' / 'fit' queries find them first
    "dimension_linear": (("DIMLINEAR", "TOLERANCE"), ("toleranced dimension", "fit h7", "iso 286 fit",
                                                     "plus minus tolerance", "limits dimension")),
    "dimension_diameter": (("DIMDIAMETER",), ("diameter tolerance", "hole fit h7", "shaft fit g6",
                                              "toleranced diameter")),
    "dimension_radius": (("DIMRADIUS",), ("radius tolerance", "toleranced radius")),
    # engine tools whose own corpus misses the everyday words
    "bom_table": ((), ("parts list", "bill of materials", "bom", "parts list table")),
    "bom_extract": ((), ("parts list", "bill of materials", "bom", "export parts list")),
}

_WORD = re.compile(r"[a-z0-9]+")
_K1, _B = 1.4, 0.6
_FIELD_WEIGHT = {"name": 3.0, "acad": 4.0, "syn": 2.0, "desc": 1.0}


def _words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall((text or "").lower().replace("_", " ")):
        out.append(w)
        if len(w) > 4 and w.endswith("s"):
            out.append(w[:-1])  # plural -> singular, crude but helps ("holes", "layers")
    return out


class _Doc:
    __slots__ = ("name", "fields", "acad", "phrases", "length")

    def __init__(self, name: str, fields: dict[str, list[str]], acad: set[str], phrases: list[str]):
        self.name, self.fields, self.acad, self.phrases = name, fields, acad, phrases
        self.length = sum(len(v) * _FIELD_WEIGHT[k] for k, v in fields.items()) or 1.0


class ToolIndex:
    """BM25 over weighted fields, plus boosts for an exact AutoCAD command and a whole phrase."""

    def __init__(self, tools: list[Any]):
        try:
            from discovery import aliases as _al  # the engine's corpus (pure data)
        except Exception:
            _al = None
        self.docs: list[_Doc] = []
        for t in tools:
            acad: list[str] = []
            syn: list[str] = []
            sources = APP_TWINS.get(t.name, ()) + (t.name,)
            for src in sources:
                rec = _al.aliases_for(src) if _al else None
                if rec:
                    acad += list(rec.acad)
                    syn += list(rec.synonyms)
            if t.name in APP_EXTRA:
                acad += list(APP_EXTRA[t.name][0])
                syn += list(APP_EXTRA[t.name][1])
            params = " ".join((t.parameters or {}).get("properties", {}).keys())
            desc = (t.description or "").split("\n\n[AutoCAD MCP Pro engine")[0]
            fields = {
                "name": _words(t.name),
                "acad": [a.lower().lstrip("-_.") for a in acad],
                "syn": _words(" ".join(syn)),
                "desc": _words(desc[:1200] + " " + params),
            }
            self.docs.append(_Doc(t.name, fields, {a.upper().lstrip("-_.") for a in acad},
                                  [s.lower() for s in syn if " " in s]))
        n = len(self.docs) or 1
        df: dict[str, int] = {}
        for d in self.docs:
            for term in {w for v in d.fields.values() for w in v}:
                df[term] = df.get(term, 0) + 1
        self.idf = {term: math.log(1 + (n - c + 0.5) / (c + 0.5)) for term, c in df.items()}
        self.avg = sum(d.length for d in self.docs) / n

    def search(self, query: str, limit: int = 8) -> list[tuple[str, float]]:
        q = _words(query)
        q_upper = {w.upper() for w in re.findall(r"[A-Za-z0-9\-]+", query or "")}
        q_plain = _WORD.findall((query or "").lower().replace("_", " "))  # no plural expansion
        q_text = " ".join(q_plain)
        q_set = set(q)
        scored = []
        for d in self.docs:
            s = 0.0
            for term in set(q):
                tf = sum(d.fields[k].count(term) * _FIELD_WEIGHT[k] for k in d.fields)
                if tf:
                    idf = self.idf.get(term, 0.0)
                    s += idf * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * d.length / self.avg))
            if q_upper & d.acad:
                s += 8.0  # the drafter typed this tool's AutoCAD command
            if any(p in q_text for p in d.phrases):
                s += 4.0  # a whole known phrase ("round the corner", "title block")
            name_words = set(d.fields["name"])
            if len(name_words) > 1 and name_words <= q_set:
                s += 5.0  # every word of the tool's name is in the query
            if q_text.replace(" ", "_") == d.name:
                s += 10.0
            if s > 0:
                scored.append((d.name, round(s, 2)))
        scored.sort(key=lambda x: -x[1])
        return scored[:max(1, min(int(limit or 8), 25))]


def _summary(desc: str) -> str:
    first = (desc or "").strip().split("\n\n")[0].replace("\n", " ")
    return first[:220] + ("..." if len(first) > 220 else "")


def _required(t: Any) -> list[str]:
    return list((t.parameters or {}).get("required", []))


def install(mcp, mode: str = "search") -> None:
    """Add search_tools / describe_tool / call_tool; in 'search' mode advertise only the short list."""
    index_holder: dict[str, ToolIndex] = {}

    def _catalogue():
        return [t for t in mcp._tool_manager.list_tools() if t.name not in DISCOVERY_TOOLS]

    def _index() -> ToolIndex:
        if "i" not in index_holder:
            index_holder["i"] = ToolIndex(_catalogue())
        return index_holder["i"]

    @mcp.tool()
    async def search_tools(query: str, limit: int = 8):
        """Find the right AutoCAD tool among all 240+ (most are not listed up front).
        Search by AutoCAD command (FILLET, BPOLY, QSELECT, MLEADER, LAYISO, PAGESETUP...) or plain words
        ("round off a corner", "bolt", "title block", "parts list", "isolate a layer", "GD&T").
        Returns the best matches with a summary and required parameters. Then use describe_tool for the
        full parameters, and call_tool (or the tool's own name) to run it."""
        by_name = {t.name: t for t in _catalogue()}
        hits = []
        for name, score in _index().search(query, limit):
            t = by_name.get(name)
            if t:
                hits.append({"name": name, "score": score, "summary": _summary(t.description),
                             "required": _required(t)})
        return {"query": query, "results": hits,
                "next": "describe_tool(name) for full parameters, then call_tool(name, arguments)."}

    @mcp.tool()
    async def describe_tool(name: str = "", names: list[str] | None = None):
        """Full description and input schema of one tool (name) or up to 25 (names) found with search_tools."""
        wanted = ([name] if name else []) + list(names or [])
        if not wanted:
            raise ToolError("Give name (one tool) or names (a list).")
        by_name = {t.name: t for t in _catalogue()}
        out = []
        for n in wanted[:25]:
            t = by_name.get(n)
            if t is None:
                close = [x for x, _ in _index().search(n.replace("_", " "), 3)]
                out.append({"name": n, "error": "no such tool (or not allowed in this access mode)",
                            "did_you_mean": close})
            else:
                desc = t.description.split("\n\n[AutoCAD MCP Pro engine")[0].strip()
                out.append({"name": n, "description": desc, "input_schema": t.parameters})
        return out[0] if len(out) == 1 else {"tools": out}

    @mcp.tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None = None):
        """Run any tool by name with its arguments (as found with search_tools / describe_tool).
        The same safety rules apply as calling the tool directly."""
        if name in DISCOVERY_TOOLS:
            raise ToolError("call_tool runs catalogue tools, not search_tools / describe_tool / call_tool.")
        if mcp._tool_manager.get_tool(name) is None:
            close = [x for x, _ in _index().search(name.replace("_", " "), 3)]
            raise ToolError(f"No tool named '{name}' (or it is not allowed in this access mode). "
                            f"Did you mean: {', '.join(close) or 'use search_tools'}?")
        res = await mcp.call_tool(name, dict(arguments or {}))
        if isinstance(res, CallToolResult):
            return res
        content = res[0] if isinstance(res, tuple) else res
        return CallToolResult(content=list(content) or [TextContent(type="text", text="(no output)")])

    if mode != "search":
        return

    async def _list_short():
        tools = await mcp.list_tools()
        keep = set(PINNED) | set(DISCOVERY_TOOLS)
        return [t for t in tools if t.name in keep]

    mcp._mcp_server.list_tools()(_list_short)
    total = len(_catalogue())
    mcp._mcp_server.instructions = (mcp._mcp_server.instructions or "") + (
        f" TOOLS: {total} AutoCAD tools exist but only a few are listed. Use search_tools (by AutoCAD command"
        " name or plain words) to find the right one, describe_tool for its parameters, then call_tool."
    )
