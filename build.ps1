# build.ps1 —— 一键打包
# 产出：dist\MiniCursor.exe（双击即可用的单文件桌面程序）
#
# 用法：powershell -ExecutionPolicy Bypass -File .\build.ps1

$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --onefile `
  --noconsole `
  --name MiniCursor `
  --collect-all webview `
  --collect-all mcp `
  --add-data "ui;ui" `
  --add-data "mcp_servers;mcp_servers" `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan.on `
  window.py

Write-Host ""
Write-Host "完成：dist\MiniCursor.exe"
