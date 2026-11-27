"""Template / CAD-standards check: compare the open drawing with YOUR company template (.dwt/.dwg/.dws).

The template is read with AutoCAD's ObjectDBX (no tab is opened, nothing is changed), from the drawings folder
only. Read-only: it reports differences, it never edits the drawing.
"""

from __future__ import annotations

from acad_server import ToolError, _acad, _doc, _safe_path, mcp, run_com

TEMPLATE_EXT = {".dwt", ".dwg", ".dws"}
SKIP_LAYERS = {"DEFPOINTS"}


def _name_set(collection) -> dict[str, str]:
    out = {}
    for item in collection:
        n = str(item.Name)
        out[n.upper()] = n
    return out


def _snapshot(db, count_entities: bool, entity_cap: int = 50000) -> dict:
    layers = {}
    for lay in db.Layers:
        try:
            layers[str(lay.Name).upper()] = {
                "name": str(lay.Name), "color": int(lay.color), "linetype": str(lay.Linetype),
                "lineweight": int(lay.Lineweight), "plot": bool(lay.Plottable)}
        except Exception:
            pass
    styles = {}
    for st in db.TextStyles:
        try:
            styles[str(st.Name).upper()] = {
                "name": str(st.Name), "font": (str(st.fontFile) or "").lower(), "height": round(float(st.Height), 4),
                "width_factor": round(float(st.Width), 4), "oblique": round(float(st.ObliqueAngle), 4)}
        except Exception:
            pass
    blocks = {k: v for k, v in _name_set(db.Blocks).items()
              if not v.startswith("*") and not v.upper().startswith("A$")}
    snap = {"layers": layers, "text_styles": styles, "dim_styles": _name_set(db.DimStyles),
            "linetypes": _name_set(db.Linetypes), "layouts": _name_set(db.Layouts), "blocks": blocks}
    if count_entities:
        used: dict[str, int] = {}
        scanned = 0
        for ent in db.ModelSpace:
            scanned += 1
            if scanned > entity_cap:
                break
            try:
                lay = str(ent.Layer)
            except Exception:
                continue
            used[lay.upper()] = used.get(lay.upper(), 0) + 1
        snap["layers_in_use"] = used
        snap["entities_scanned"] = min(scanned, entity_cap)
        snap["entity_cap_reached"] = scanned > entity_cap
    return snap


def _missing_extra(t: dict, d: dict, limit: int) -> dict:
    missing = sorted(t[k] for k in t if k not in d)
    extra = sorted(d[k] for k in d if k not in t)
    return {"missing_from_drawing": missing[:limit], "not_in_template": extra[:limit]}


def compare(t: dict, d: dict, limit: int = 50) -> dict:
    """Pure comparison of two snapshots (unit-testable without AutoCAD)."""
    layer_diffs = []
    for key, tl in t["layers"].items():
        dl = d["layers"].get(key)
        if dl is None or key in SKIP_LAYERS:
            continue
        for prop in ("color", "linetype", "lineweight", "plot"):
            tv, dv = tl[prop], dl[prop]
            if (str(tv).upper() if prop == "linetype" else tv) != (str(dv).upper() if prop == "linetype" else dv):
                layer_diffs.append({"layer": dl["name"], "property": prop, "template": tv, "drawing": dv})
    style_diffs = []
    for key, ts in t["text_styles"].items():
        ds = d["text_styles"].get(key)
        if ds is None:
            continue
        for prop in ("font", "height", "width_factor", "oblique"):
            if ts[prop] != ds[prop]:
                style_diffs.append({"style": ds["name"], "property": prop, "template": ts[prop], "drawing": ds[prop]})
    in_use = d.get("layers_in_use", {})
    nonstandard = sorted(({"layer": d["layers"].get(k, {}).get("name", k), "entities": n}
                          for k, n in in_use.items() if k not in t["layers"] and k not in SKIP_LAYERS),
                         key=lambda r: -r["entities"])
    layers = _missing_extra({k: v["name"] for k, v in t["layers"].items()},
                            {k: v["name"] for k, v in d["layers"].items()}, limit)
    layers["different_properties"] = layer_diffs[:limit]
    text = _missing_extra({k: v["name"] for k, v in t["text_styles"].items()},
                          {k: v["name"] for k, v in d["text_styles"].items()}, limit)
    text["different_properties"] = style_diffs[:limit]
    issues = len(layer_diffs) + len(style_diffs) + len(nonstandard)
    return {
        "compliant": issues == 0,
        "summary": {
            "layer_property_differences": len(layer_diffs),
            "text_style_differences": len(style_diffs),
            "nonstandard_layers_in_use": len(nonstandard),
            "template_layers_missing": len(layers["missing_from_drawing"]),
            "extra_dimension_styles": len(_missing_extra(t["dim_styles"], d["dim_styles"], 10**6)["not_in_template"]),
        },
        "nonstandard_layers_in_use": nonstandard[:limit],
        "layers": layers,
        "text_styles": text,
        "dimension_styles": _missing_extra(t["dim_styles"], d["dim_styles"], limit),
        "linetypes": _missing_extra(t["linetypes"], d["linetypes"], limit),
        "layouts": _missing_extra(t["layouts"], d["layouts"], limit),
        "blocks": _missing_extra(t["blocks"], d["blocks"], limit),
        "entities_scanned": d.get("entities_scanned"),
        "not_checked": ["dimension style settings (names are compared, not their values)",
                        "drawing units", "paper-space entities (model space is scanned)"],
    }


@mcp.tool()
async def template_check(template: str, max_items: int = 50) -> dict:
    """Check the open drawing against YOUR company template / CAD standard (a .dwt, .dwg or .dws file in the
    drawings folder), like AutoCAD's CHECKSTANDARDS. Reports: layers whose colour / linetype / lineweight / plot
    setting differ from the template, entities on layers the template doesn't have, text styles whose font /
    height / width differ, and dimension styles, linetypes, layouts and blocks that are missing or extra.
    Read-only: nothing in the drawing is changed, and the template is read without opening a tab.
    `compliant` is true when no layer, text-style or non-standard-layer issue was found."""
    full = _safe_path(template, must_exist=True, exts=TEMPLATE_EXT)
    limit = max(5, min(int(max_items), 500))

    def f():
        app = _acad()
        major = str(app.Version).split(".")[0]
        try:
            dbx = app.GetInterfaceObject(f"ObjectDBX.AxDbDocument.{major}")
        except Exception as e:
            raise ToolError(f"AutoCAD's ObjectDBX reader is not available ({e}).") from e
        try:
            dbx.Open(full)
        except Exception as e:
            raise ToolError("Could not read the template (is it open in AutoCAD, or saved by a newer "
                            "AutoCAD version?).") from e
        try:
            t = _snapshot(dbx, count_entities=False)
        finally:
            del dbx
        doc = _doc()
        d = _snapshot(doc, count_entities=True)
        result = compare(t, d, limit)
        result["template"] = full.split("\\")[-1]
        result["drawing"] = str(doc.Name)
        return result

    return await run_com(f)
