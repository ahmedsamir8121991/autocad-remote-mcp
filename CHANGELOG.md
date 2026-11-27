# Changelog

All notable changes to AutoCAD Remote MCP.

## 6.2.0 (2026-11-26)

- **Toleranced dimensions:** linear, diameter and radius dimensions with ISO 129 tolerances (symmetric ±, deviation, limits, basic) and ISO 286 fits such as `20 H7` or `Ø12 g6`.
- **Template check:** `template_check` compares the open drawing with your company template (`.dwt`, `.dwg`, `.dws`): layers, text and dimension styles, linetypes, layouts and blocks. Read-only.
- **Lasting history:** the activity log is saved on the AutoCAD PC for 90 days and survives restarts. Checkpoints are also saved to disk and can be restored as a new drawing.
- **One AutoCAD link:** Pro engine tools always act on the same drawing as the rest of the app.
- Source code, verified GitHub builds with signed attestations, pinned build dependencies, CodeQL and Dependabot.

## 6.1.0

- **Security:** internet mode removed; only local network or this PC. Replies are now encrypted as well as requests, and unencrypted requests are refused.

## 6.0.0

- Built-in AutoCAD MCP Pro engine ([U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP), MIT): 128 extra tools, 247 in total, all under the same safe mode.
- Tool search: Claude sees 12 tools up front and finds the rest with `search_tools`, `describe_tool` and `call_tool`.

## 5.0.0

- Skills layer: requirement preflight and measured verification, all-or-nothing batches, checkpoints, drawing diff, critique and topology checks, takeoffs, spec-to-drawing workflows (plans, parts, P&ID) and DWG + PDF + manifest delivery.
