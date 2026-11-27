=====================================================================
  AutoCAD Remote MCP  v6.2  -  Connect AutoCAD to Claude
              Protected by YOUR password  +  Encrypted  +  Safe mode
=====================================================================

WHAT IT DOES
------------
Lets Claude on one computer control AutoCAD on another computer.

NETWORK MODE (menu option [7], needs your password):
   LOCAL NETWORK  (default)  Claude PC and AutoCAD PC on the SAME Wi-Fi
                             or office network. Nothing goes over the
                             internet. The Claude PC finds the AutoCAD PC
                             by itself.
   THIS PC only              Claude and AutoCAD on the same computer.
   (Internet mode was REMOVED in v6.1 for security: nothing ever goes
    over the internet. An old internet setting falls back to LOCAL.)

   Claude PC  ---- same Wi-Fi / LAN ---->  AutoCAD PC

Use the SAME AutoCADRemoteMCP.exe file on both computers.
No accounts and no router settings.
In LOCAL NETWORK mode Windows may ask about the firewall once: tick
"Private networks" and click "Allow access". Your Wi-Fi/LAN must be set
to "Private" in Windows (Settings -> Network -> Properties).

To connect, you need TWO things:
   1. the PAIRING CODE (made by the app, starts with ACAD-)
   2. YOUR PASSWORD    (you choose it; only you know it)


=====================================================================
 STEP 1 - ON THE AUTOCAD PC   (about 1 minute)
=====================================================================

 1. Copy AutoCADRemoteMCP.exe to that PC (USB, email, Google Drive...).
 2. Double-click it.
      If Windows says "Windows protected your PC":
      click "More info" -> "Run anyway"  (the file is not code-signed).
 3. Type 1 and press Enter   ("This PC has AUTOCAD").
 4. CHOOSE YOUR PASSWORD (at least 8 characters) and type it twice.
      Typing is hidden: nothing appears on screen while you type.
      Write it down somewhere safe. It CANNOT be recovered.
 5. Wait while it:
      - installs itself
      - sets itself to start automatically with Windows
 6. It shows your PAIRING CODE (ACAD-...). The code is also copied to
    the clipboard. Send it to yourself.
 7. Wait for "ONLINE" (a few seconds on the local network).
    You can minimize the window, but do NOT close it.

 Open AutoCAD as usual. That's it for this PC.

 DRAWINGS FOLDER: Claude can open and save drawings ONLY in one folder.
 By default it is  Documents\AutoCAD Remote  (created automatically).
 Put the drawings you want Claude to work on there, or choose another
 folder with menu option [5] (see SAFE MODE below).

 After a restart, the sharing window opens by itself at login and asks
 for YOUR PASSWORD before sharing starts (PASSWORD EVERY START, on by
 default - see MENU OPTIONS). Until you type it, Claude can't reach
 this PC. The pairing code is NOT shown in that window.


=====================================================================
 STEP 2 - ON THE CLAUDE PC   (about 1 minute)
=====================================================================

 1. Copy the same AutoCADRemoteMCP.exe to the Claude PC.
 2. Double-click it.
 3. Type 2 and press Enter   ("This PC has CLAUDE").
 4. Paste the pairing code (right-click pastes in the black window)
    and press Enter.
 5. Type YOUR PASSWORD (hidden) and press Enter.
      - Wrong password: "Wrong password or pairing code".
        You get 3 tries, and nothing is saved.
      - Right password: it connects and adds the AutoCAD connector to
        Claude by itself (Claude Desktop, and Claude Code if installed).
 6. FULLY quit Claude: right-click the Claude icon near the clock -> Quit.
    Then open Claude again.

 PASSWORD EVERY START (on by default): Claude opens normally, and the
 first time Claude wants to use AutoCAD, a small window asks for your
 password. It is remembered only until you quit Claude. 3 wrong tries
 lock it for 1 minute. Opening AutoCADRemoteMCP.exe also asks for it.
 Turn it OFF with menu option [8] to type the password only once.

 Try asking Claude:
   "Check the AutoCAD status, then draw a 100 x 50 rectangle on a new
    layer called WALLS in red, and zoom extents."


=====================================================================
 HOW YOUR PASSWORD PROTECTS YOU
=====================================================================
 - Connecting needs BOTH the pairing code AND your password. Someone
   who only has the pairing code can't do anything.
 - Your password is NEVER saved, on either PC, and never sent anywhere.
 - With PASSWORD EVERY START on (default), the access key is NOT saved
   either - only a check value. The key is rebuilt from your password
   each time and kept in memory only. Someone (or malware) who copies
   the settings files can't connect without your password.
   With it OFF, the key is saved encrypted by Windows, for YOUR Windows
   account on THAT PC only.
 - PASSWORD EVERY START (on by default): even someone sitting at your
   unlocked PC can't open the app, start sharing, or let Claude use
   AutoCAD without typing your password. Also lock Windows (Win + L)
   when you step away.
 - Password guessing is blocked: after 5 wrong attempts, that address
   is locked out for 15 minutes. The AutoCAD PC window shows a
   [security] warning when this happens.
 - On the AutoCAD PC, these need your password: showing the pairing
   code, changing the password, and uninstalling.
 - Every request is SIGNED and ENCRYPTED (AES-256-GCM) with the key,
   and every reply is encrypted too (new in v6.1). Someone on the same
   Wi-Fi sees only scrambled bytes - no drawings, text or file names.
   A captured request can't be read, changed or replayed, and the key
   itself never travels. Unencrypted requests are refused.

 Choose a strong password: 12 or more characters, mixing words, numbers
 and symbols. Example style:  Blue-Tower-Drafting-73!
 (Don't use this example.)


=====================================================================
 SAFE MODE (always on)
=====================================================================
 Even someone with your code AND password - or Claude following a bad
 instruction - can only DRAW. They cannot take over your PC:

 - NO free-form commands, LISP or scripts. The old "send_command" tool
   is removed. Only these maintenance commands are possible:
   regen, undo, redo, audit, purge.
 - FILES: drawings can be opened, saved and inserted ONLY inside the
   drawings folder, and only .dwg / .dxf / .dwt files. Other folders,
   network paths, "..\" tricks and folder shortcuts (junctions) are
   all refused.
   A drawing you opened yourself from another folder can still be
   edited by Claude, but Claude can't save it in place - save it
   yourself, or let Claude "save as" into the drawings folder.
 - SETTINGS: Claude can change only everyday drafting settings
   (LTSCALE, CLAYER, OSMODE, INSUNITS, DIMSCALE, TEXTSIZE, ...).
   Security settings such as SECURELOAD or TRUSTEDPATHS are read-only.
 - Claude is told that text inside drawings is data, and never to follow
   instructions written in a drawing.
 - Advanced work (offset, mirror, arrays, hatch, ellipse, spline,
   leaders, tables, layer states, block creation, PDF export) uses
   dedicated safe tools - nothing is typed into the command line.
   Not available: fillet, chamfer, trim, extend, stretch.

 To change the drawings folder: menu option [5] (needs your password).
 The change applies at once, even while sharing is running.


=====================================================================
 MENU OPTIONS
=====================================================================
 On the AutoCAD PC, double-clicking the exe just starts sharing.
 To get the menu there, open a Command Prompt and run:
     "%LOCALAPPDATA%\AutoCADRemoteMCP\AutoCADRemoteMCP.exe" --setup

  [1] This PC has AUTOCAD   set up sharing, or SHOW the pairing code
                            (needs your password)
  [2] This PC has CLAUDE    connect with pairing code + password
  [3] Change password       (AutoCAD PC, needs the current password)
                            Makes a NEW pairing code too: the old code and
                            password stop working at once. Then redo [2]
                            on every Claude PC.
  [4] Uninstall             (on the AutoCAD PC, needs your password)
  [5] Drawings folder       (AutoCAD PC, needs your password)
                            The only folder Claude may open/save files in.
  [6] Access mode           (AutoCAD PC, needs your password)
                            FULL      = Claude can draw, edit, save, export
                            READ-ONLY = Claude can only look, measure,
                                        search and take screenshots
  [7] Network mode          (AutoCAD PC, needs your password)
                            LOCAL NETWORK / THIS PC only
  [8] Password every start  (needs your password)
                            ON  = password each time the app opens,
                                  each time sharing starts, and once
                                  per Claude session (default)
                            OFF = password only for settings (the key
                                  is then saved, Windows-encrypted)
  [9] Exit

 If sharing is already running, double-clicking the exe shows this menu.

 FORGOT YOUR PASSWORD?  It can't be recovered. On the AutoCAD PC, delete
 the folder %APPDATA%\AutoCADRemoteMCP and choose [1] again to set a new
 password. Then redo [2] on the Claude PC.
 (Deleting that folder requires being logged in to that PC as you.)


=====================================================================
 EVERYDAY USE
=====================================================================
 - The AutoCAD PC must be ON, on the same network as the Claude PC,
   with AutoCAD open and the AutoCAD Remote MCP window running (it opens
   at login and asks for your password).
 - The Claude PC finds the AutoCAD PC on the network automatically.
 - If the AutoCAD PC is off, Claude says "Cannot reach AutoCAD".
 - UPGRADING FROM v6.0 OR OLDER: install v6.1 on BOTH PCs. A v6.1
   AutoCAD PC refuses unencrypted requests from an older Claude PC.


=====================================================================
 WHAT CLAUDE CAN DO (120 tools)
=====================================================================
 Vision   : capture_screenshot - Claude SEES the drawing (PNG of the
            AutoCAD drawing area) to check its own work
 Drawings : autocad_status, new_drawing, open_drawing, save_drawing,
            list_drawings, list_drawing_files, switch_drawing,
            drawing_info, export_pdf (model, or a layout sheet;
            A4/A3/A2/A1/A0/Letter/Tabloid, saved in the drawings folder)
 Drawing  : draw_line, draw_circle, draw_arc, draw_ellipse,
            draw_polyline (with arc segments and width), draw_rectangle,
            draw_spline, add_text (TEXT or MTEXT), add_leader, add_table,
            add_hatch (SOLID, ANSI31, AR-CONC, BRICK, ...)
 Dimension: add_dimension (aligned), add_dimension_linear (horizontal /
            vertical / rotated), add_dimension_angular,
            add_dimension_radius, add_dimension_diameter
 Modify   : transform_entity (move/copy/rotate/scale), offset_entity,
            mirror_entities, array_rectangular, array_polar,
            explode_entity, delete_entities, fillet_lines, chamfer_lines,
            trim_line, extend_line, break_line
 Properties: modify_entity, change_properties (many at once: layer,
            color, linetype, lineweight), match_properties
 Select   : list_entities, get_entity, select_entities (by layer, type,
            color, window, text), get_bounding_box
 Text     : find_text, replace_text (TEXT, MTEXT, leaders, attributes)
 Layers   : create_layer, set_current_layer, list_layers,
            set_layer_state (on/off, freeze, lock, color), rename_layer,
            delete_layer
 Blocks   : create_block (with attribute definitions), insert_block
            (with attribute values), set_block_attributes, list_blocks
 Styles   : list_styles, create_text_style, create_dimension_style,
            set_current_style, load_linetype
 Measure  : measure_area, measure_polygon, measure_distance,
            label_areas (writes "12.50 m2" inside each room)
            Results also in m2 / ft2 when the drawing units (INSUNITS)
            are set, e.g. 4 = millimetres, 6 = metres.
 View     : zoom_extents, zoom_window, zoom_to_entities, set_view
            (top, front, iso_se ... for 3D)
 Layouts  : list_layouts, create_layout (PDF-ready sheet in mm),
            create_viewport, set_current_layout
 Xrefs    : attach_xref, list_xrefs, detach_xref (drawings folder only)
 3D       : create_solid (box, cylinder, sphere, cone, wedge), extrude,
            solid_boolean (union, subtract, intersect)
 Other    : run_command (regen / undo / redo / audit / purge only),
            get_variable, set_variable (drafting settings only),
            cancel_command (presses Esc in AutoCAD to end a stuck command)

 SKILLS (measured workflow - v5.0)
 Verify   : drawing_preflight (checks a requirement list, asks about
            missing values), drawing_verify_requirements (MEASURES each
            requirement on the geometry: target / measured / delta /
            pass, fail, ambiguous), point_from_snap
 Batches  : cad_batch (many steps, all-or-nothing, one undo, dry run,
            "$name" references), entity_batch_create,
            entity_modify_where, layer_merge, checkpoint_create /
            checkpoint_restore, drawing_history (log of every call)
 Analyse  : drawing_understand (someone else's drawing),
            drawing_topology_check (gaps, duplicates, open shapes),
            drawing_diff (compare with a saved revision),
            drawing_critique (drafting quality), construction_clear
 Quantity : drawing_scale_check, pipe_takeoff, cable_takeoff,
            quantity_takeoff (lengths per layer + block counts, in m)
 Plans    : arch_plan_from_spec (rooms, walls, doors, windows, area
            labels), arch_rooms_detect, arch_schedule (room table)
 Parts    : mech_part_draw (plate, rounded corners, tagged holes),
            mech_part_inspect, mech_part_revise, dimension_auto
 P&ID     : pid_from_spec (tanks, pumps, valves, instruments, lines
            with flow arrows), pid_graph (connections read back)
 Deliver  : drawing_finalize (save + re-measure on the reopened file),
            drawing_deliver (DWG + PDF + manifest.json with checksums
            and the requirement report)
 System   : system_capabilities, recipes (step-by-step workflows:
            part, floor-plan, foreign-drawing, takeoff, edit-safely,
            deliver)

 Try: "Read recipe 'part', then draw a 120x80 plate with four 8.5 mm
 holes 20 mm from the edges, verify it and deliver it as Bracket-A."

 PRO ENGINE (new in v6.0 - 131 more tools, 251 in total)
 Built in: the AutoCAD MCP Pro engine (github.com/U-C4N/Autocad-MCP,
 MIT licence, see LICENSE-AutoCAD-MCP-Pro.txt). Same safe mode: drawings
 folder only, no LISP / commands / scripts, READ-ONLY mode respected.
 Mechanical: gd_frame, datum_feature, surface_texture, weld_symbol,
            gears (spur/helical front view, section A-A), keyed bores,
            std_part_insert (ISO bolts, nuts, washers, bearings),
            std_feature_draw, mech_part_from_spec, mech_view_add,
            mech_hole_pattern, mech_dimension_part, centre_marks,
            section_line
 Sheets   : sheet_frame (ISO A4-A0), titleblock_apply, revision_add,
            balloon_add, bom_table, bom_extract, data_extract,
            page_setup_apply / list, plot_style_list, batch_plot,
            layout_copy / rename / delete, viewport_list / delete /
            lock / set_scale, drawing_export_dxf / dwg, xref_manage
 Standards: drawing_apply_standard (ISO/ANSI styles, units, layers),
            drawing_apply_iso_layers, dimstyle_modify, mleaderstyle_*,
            templates (template_list, template_apply_layers,
            drawing_template_list / save), drawing_settings,
            drawing_properties_get / set
 Plans    : arch_wall, arch_opening, arch_room, arch_stair, arch_symbol,
            arch_grid, arch_dimension_chains, arch_catalogue_list/insert
 P&ID     : pid_symbol_list / insert, pid_line_draw (routes around
            equipment), pid_line_list, pid_tag_parse,
            pid_instrument_index, pid_equipment_list, pid_project
 Edit     : entity_edit_geometry, entity_edit_text, entity_delete_many,
            entity_change_space, xdata get/set, boundary_trace,
            boundary_from_entities, block_create_from_entities,
            hatch_edit / add_boundary / set_gradient / material,
            leader_create_mleader, entity_create_wipeout / point,
            construction_xline, text_set_background, image_attach
 Layers   : layer_isolate / hide / show, layer states save / restore
 Views    : UCS (ucs_list / set / restore), named views, selections
            (window, polygon, filter, smart), point_intersection /
            tangent, analysis stats, view_zoom_and_screenshot
 Quality  : drawing_plan, drawing_refine, validation_check,
            transactions (begin / commit / rollback), checkpoint_list
 Some engine tools work only without AutoCAD (e.g. revision clouds);
 they say so clearly when called.

 NEW IN v6.2
   Toleranced dimensions : dimension_linear / dimension_diameter /
            dimension_radius with ISO 129 tolerances (symmetric +/-,
            deviation, limits, basic) and ISO 286 fits, e.g. "20 H7",
            "dia 12 g6". For shop drawings.
   Template check : template_check compares the open drawing with YOUR
            company template (.dwt / .dwg / .dws in the drawings folder):
            layer colour/linetype/lineweight/plot, entities on layers
            the template doesn't have, text styles, dimension styles,
            linetypes, layouts and blocks. Read-only - nothing changes.
   Lasting history : the activity log is saved on the AutoCAD PC
            (%LOCALAPPDATA%\AutoCADRemoteMCP\history, kept 90 days) and
            survives restarts. checkpoint_create also saves a snapshot
            on disk; checkpoint_restore(from_saved=true) reopens it as a
            new drawing even after the drawing was closed or the PC
            restarted.
   One AutoCAD link : the engine tools always work on the same drawing
            as the rest of the app (no more "active document changed").

 TOOL SEARCH (new in v6.0)
 Claude is shown only 12 tools up front instead of all 251, which saves
 tokens and helps it pick the right tool. It finds the rest with:
   search_tools  - by AutoCAD command (FILLET, BPOLY, QSELECT, LAYISO,
                   PAGESETUP, MLEADER ...) or plain words ("round off a
                   corner", "title block", "parts list", "bolt", "GD&T")
   describe_tool - full parameters of a tool it found
   call_tool     - runs it (same safety rules as calling it directly)
 Every tool can still be called by its own name.
 To list all 251 tools up front instead, add  "discovery": "full"  to
 %APPDATA%\AutoCADRemoteMCP\server.json on the AutoCAD PC and restart
 sharing.

 Not available (on purpose, for safety): free-form commands, LISP,
 scripts. Fillet / chamfer / trim / extend / break work on LINES.
 Not included: ISO tolerance/GD&T tables, dynamic blocks, multi-sheet
 P&ID projects, electrical schematics.

 NOTE: Files are always in the drawings folder on the AutoCAD PC.
 Just use file names, e.g. "open plan1.dwg" or "save as house.dwg".


=====================================================================
 TROUBLESHOOTING
=====================================================================
 "Wrong password or pairing code"
   -> Check the password (it is case-sensitive) and the pairing code.
      To see the code again: on the AutoCAD PC run --setup -> [1].

 "password rejected" in Claude
   -> The password was changed on the AutoCAD PC. Run the exe on the
      Claude PC, choose [2], and enter the new code and password.

 "too many wrong attempts - locked for 15 minutes"
   -> Wait 15 minutes, then try again with the correct password.

 "Cannot reach AutoCAD" / "AutoCAD PC is offline"
   -> On the AutoCAD PC, check that the AutoCAD Remote MCP window is open
      and shows "[ready] ONLINE". If not, double-click the exe.
   -> Check both PCs are on the same Wi-Fi/LAN, set to "Private".

 "encryption required" / "requires encryption"
   -> One PC still has v6.0 or older. Install v6.1 on both PCs and
      choose [2] again on the Claude PC.

 "AutoCAD is not running on the server PC"
   -> Open AutoCAD on the AutoCAD PC.

 "files can only be opened/saved inside the drawings folder"
   -> Safe mode. Move the drawing into the drawings folder, or choose
      another folder with menu option [5].

 "... is not allowed" (a command or setting)
   -> Safe mode blocks free-form commands, LISP and security settings.
      Ask Claude to use the drawing tools instead.

 Claude doesn't show the AutoCAD tools
   -> Fully quit Claude (tray icon -> Quit) and open it again.

 Commands are slow or rejected
   -> AutoCAD is busy or stuck at a prompt (e.g. real-time zoom).
      Ask Claude to run cancel_command: it presses Esc in AutoCAD for you,
      without touching the mouse, and checks AutoCAD is free again.
      If a dialog box is open, close it in AutoCAD.

 Antivirus blocks the exe
   -> Files built with PyInstaller are sometimes flagged by mistake.
      Allow the file in your antivirus settings.

 Company network blocks it
   -> The app needs no internet at all. On the local network it uses
      TCP port 8765 (requests) and UDP port 8766 (finding the PC).


=====================================================================
 TECHNICAL DETAILS
=====================================================================
 - Key derivation: scrypt (N=32768, r=8) from password + pairing code.
 - Encryption: AES-256-GCM with a key derived from the access key
   (HMAC-SHA256). Each request is bound to its signature (time + one-
   time nonce); each reply is bound to its request.
 - Signing: HMAC-SHA256 over time, nonce, method, path and body hash;
   requests older than 2 minutes or reusing a nonce are refused.
 - Key storage: PASSWORD EVERY START on = check value only (HMAC of the
   key, which can't be turned back into the key); off = key encrypted
   with Windows DPAPI (current user).
 - Pairing code on disk: encrypted with Windows DPAPI (current user,
   new in v6.2.1). A copied settings folder is useless on another PC.
 - Finding the PC: UDP broadcast on the local network; only an answer
   signed with your key is accepted.
 - Files:     %LOCALAPPDATA%\AutoCADRemoteMCP\  (the installed exe)
   Settings:  %APPDATA%\AutoCADRemoteMCP\
   Auto-start: Startup folder shortcut "AutoCAD Remote MCP"
   Claude configs are backed up first (*.bak-autocad).
=====================================================================
