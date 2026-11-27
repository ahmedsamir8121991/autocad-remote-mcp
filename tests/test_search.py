"""Tool search: ranking for drafter-style queries, the short tool list, describe_tool and call_tool.
Run:  .venv\\Scripts\\python.exe tests\\test_search.py   (needs AutoCAD open for the call_tool check)"""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("ACAD_PRO_ENGINE", r"C:\autocad-mcp-pro")

import mcp.types as T  # noqa: E402

import acad_server  # noqa: E402

acad_server.build_app("x" * 64, folders_provider=lambda: [tempfile.gettempdir()])
m = acad_server.mcp

# query -> tools that count as a correct answer (one must be in the top 3)
CASES = {
    "FILLET": {"fillet_lines", "entity_fillet"},
    "BPOLY": {"boundary_trace"},
    "QSELECT": {"selection_filter", "entity_select_smart", "entity_modify_where", "select_entities"},
    "MATCHPROP": {"match_properties"},
    "LAYISO": {"layer_isolate"},
    "PAGESETUP": {"page_setup_apply", "page_setup_list"},
    "MLEADER": {"leader_create_mleader", "add_leader"},
    "REVCLOUD": {"entity_create_revcloud", "revision_add"},
    "round off a corner": {"fillet_lines", "entity_fillet"},
    "title block": {"titleblock_apply", "titleblock_apply_iso_a3", "sheet_frame"},
    "parts list": {"bom_table", "bom_extract"},
    "bolt": {"std_part_insert", "std_part_list"},
    "GD&T flatness": {"gd_frame"},
    "isolate a layer": {"layer_isolate"},
    "draw a circle": {"draw_circle"},
    "hole": {"draw_circle", "mech_hole_pattern", "std_feature_draw"},
    "gear": {"gear_draw_spur_front_view", "gear_draw_helical_front_view"},
    "save layer state": {"layer_state_save"},
    "wall with a door": {"arch_wall", "arch_opening", "arch_plan_from_spec"},
    "export to pdf": {"export_pdf", "batch_plot"},
    "move copy rotate": {"transform_entity"},
    "weld": {"weld_symbol"},
}


def text(r):
    r = r[0] if isinstance(r, tuple) else r
    if isinstance(r, T.CallToolResult):
        r = r.content
    return r[0].text


async def main():
    listed = await m._mcp_server.request_handlers[T.ListToolsRequest](T.ListToolsRequest(method="tools/list"))
    allnames = {t.name for t in await m.list_tools()}
    print(f"registered {len(allnames)}, listed up front {len(listed.root.tools)}:",
          sorted(t.name for t in listed.root.tools))
    ok = 0
    for q, good in CASES.items():
        res = json.loads(text(await m.call_tool("search_tools", {"query": q, "limit": 3})))
        top = [h["name"] for h in res["results"]]
        hit = bool(set(top) & good)
        ok += hit
        print(f"  {'PASS' if hit else 'FAIL'}  {q!r:24} -> {top}")
    print(f"ranking: {ok}/{len(CASES)} queries have a right tool in the top 3")

    d = json.loads(text(await m.call_tool("describe_tool", {"name": "gd_frame"})))
    print("describe gd_frame: params =", sorted(d["input_schema"]["properties"])[:8])
    d = json.loads(text(await m.call_tool("describe_tool", {"name": "gd_fram"})))
    print("describe typo:", d.get("error"), d.get("did_you_mean"))

    r = await m.call_tool("call_tool", {"name": "autocad_status", "arguments": {}})
    print("call_tool autocad_status:", text(r)[:120].replace("\n", " "))
    try:
        await m.call_tool("call_tool", {"name": "system_run_lisp", "arguments": {"code": "(+ 1 2)"}})
        print("call_tool system_run_lisp: NOT BLOCKED")
    except Exception as e:
        print("call_tool system_run_lisp: blocked ->", str(e)[:110])


asyncio.run(main())
