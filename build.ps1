# Builds dist\AutoCADRemoteMCP.exe (one file) from src\ plus the AutoCAD MCP Pro engine.
# Build environment: .venv here, made with:
#   git clone https://github.com/U-C4N/Autocad-MCP C:\autocad-mcp-pro
#   git -C C:\autocad-mcp-pro checkout 6f6a538255ca9f885232e845ac32f10b4433fe3e
#   py -3.12 -m venv .venv
#   .venv\Scripts\python.exe -m pip install -r requirements-build.txt
#   .venv\Scripts\python.exe -m pip install --no-deps C:\autocad-mcp-pro
# GitHub Actions runs exactly these steps in .github/workflows/build.yml and signs the result.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = '.venv\Scripts\python.exe'
$sp = & $py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"

& $py -m PyInstaller --noconfirm --clean --onefile --console `
    --name AutoCADRemoteMCP `
    --paths src `
    --hidden-import acad_server --hidden-import acad_pro --hidden-import acad_skills --hidden-import acad_bridge --hidden-import acad_search --hidden-import acad_crypto --hidden-import acad_template `
    --hidden-import server --hidden-import config --hidden-import security --hidden-import security_lisp `
    --hidden-import policy --hidden-import http_guard --hidden-import version `
    --collect-submodules backends --collect-submodules engineering --collect-submodules discovery `
    --collect-data engineering --collect-data discovery `
    --add-data "$sp\templates;templates" `
    --collect-submodules fastmcp.server --collect-submodules fastmcp.client --collect-submodules fastmcp.tools `
    --collect-submodules fastmcp.utilities --collect-data fastmcp `
    --collect-submodules mcp.server --collect-submodules mcp.client --collect-submodules mcp.shared `
    --hidden-import mcp.types `
    --collect-data ezdxf `
    --copy-metadata fastmcp --copy-metadata fastmcp-slim --copy-metadata mcp --copy-metadata autocad-mcp-pro `
    --hidden-import win32timezone `
    src\launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
Copy-Item README.txt dist\README.txt -Force
Copy-Item 'C:\autocad-mcp-pro\LICENSE' dist\LICENSE-AutoCAD-MCP-Pro.txt -Force
Get-Item dist\AutoCADRemoteMCP.exe | Select-Object Name, Length, LastWriteTime
