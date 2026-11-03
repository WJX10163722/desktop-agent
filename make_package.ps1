# make_package.ps1 —— 一键生成"要交的压缩包"
#
# 为什么要有这个脚本：
#   交作业当天最容易乱：报告在根目录、视频在下载文件夹、exe 在 dist、
#   源码还在 git 里，手动挑文件必漏。脚本把它们按编号摆好，一次压完。
#
# 用法（在项目根目录打开 PowerShell）：
#   .\make_package.ps1 -Sid 20230001 -Name 张三
#
# 产物：
#   dist\提交_20230001_张三_MiniCursor.zip

param(
    [string]$Sid = "学号",
    [string]$Name = "姓名",
    [string]$VideoPath = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd"

# ---------- 1. 组装目录 ----------
$folderName = "提交_${Sid}_${Name}_MiniCursor"
$stage = Join-Path $root "dist\_package\$folderName"

if (Test-Path (Join-Path $root "dist\_package")) {
    Remove-Item -Recurse -Force (Join-Path $root "dist\_package")
}
New-Item -ItemType Directory -Force -Path $stage | Out-Null

Write-Host "[1/5] 组装目录：$folderName" -ForegroundColor Cyan
foreach ($sub in @("01_报告", "02_演示视频", "03_源码", "04_程序")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage $sub) | Out-Null
}

# ---------- 2. 报告 ----------
Write-Host "[2/5] 复制 report.md" -ForegroundColor Cyan
if (Test-Path (Join-Path $root "report.md")) {
    Copy-Item (Join-Path $root "report.md") (Join-Path $stage "01_报告\report.md")
} else {
    Write-Host "  !! 缺少 report.md" -ForegroundColor Yellow
}

# ---------- 3. 演示视频 ----------
Write-Host "[3/5] 复制演示视频" -ForegroundColor Cyan
$video = $VideoPath
if (-not $video) {
    # 常见位置自动找：项目根目录、桌面、下载
    $candidates = @(
        (Join-Path $root "demo.mp4"),
        (Join-Path $root "dist\demo.mp4"),
        (Join-Path $env:USERPROFILE "Desktop\demo.mp4"),
        (Join-Path $env:USERPROFILE "Videos\demo.mp4"),
        (Join-Path $env:USERPROFILE "Downloads\demo.mp4")
    )
    $video = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($video -and (Test-Path $video)) {
    Copy-Item $video (Join-Path $stage "02_演示视频\demo.mp4")
    $mb = [math]::Round((Get-Item $video).Length / 1MB, 1)
    Write-Host "  已放入 demo.mp4（$mb MB）" -ForegroundColor Green
} else {
    Write-Host "  !! 没找到 demo.mp4，请录完再跑一次（或用 -VideoPath 指定路径）" -ForegroundColor Yellow
}

# ---------- 4. 源码（只打已提交的文件，天然不带 .env / dist / build） ----------
Write-Host "[4/5] 导出源码（git archive：保证不含 .env 和构建产物）" -ForegroundColor Cyan
$git = "D:\Git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }
try {
    $zip = Join-Path $env:TEMP "src_$stamp.zip"
    & $git -C $root archive --format=zip -o $zip HEAD
    Expand-Archive -Path $zip -DestinationPath (Join-Path $stage "03_源码") -Force
    Remove-Item $zip -Force
    Write-Host "  源码导出完成（不含 .env，可安全提交）" -ForegroundColor Green
} catch {
    Write-Host "  !! git archive 失败：$_" -ForegroundColor Yellow
    Write-Host "     退而求其次：手动复制 agent/ server/ ui/ tests/ 等目录过去" -ForegroundColor Yellow
}

# ---------- 5. 可执行程序 ----------
Write-Host "[5/5] 复制 MiniCursor.exe" -ForegroundColor Cyan
$exe = Join-Path $root "dist\MiniCursor.exe"
if (Test-Path $exe) {
    Copy-Item $exe (Join-Path $stage "04_程序\MiniCursor.exe")
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "  已放入 MiniCursor.exe（$mb MB）" -ForegroundColor Green
} else {
    Write-Host "  !! dist\MiniCursor.exe 不存在，先跑 .\build.ps1 打包" -ForegroundColor Yellow
}

# ---------- 提交说明 ----------
$readme = @"
提交材料说明
========================================
学号：$Sid
姓名：$Name
项目：Mini Cursor（桌面编程智能体）
日期：$stamp

一、要交的两个链接
  仓库：   https://github.com/WJX10163722/desktop-agent
  Release：https://github.com/WJX10163722/desktop-agent/releases/tag/v1.0.0

二、压缩包内容
  01_报告/report.md          设计报告
  02_演示视频/demo.mp4       现场演示录屏
  03_源码/                   完整源码（不含 .env，无密钥泄漏）
  04_程序/MiniCursor.exe     可双击运行的桌面程序

三、怎么运行
  1) 双击 04_程序\MiniCursor.exe 即可运行（不需要装 Python）
  2) 第一次使用：在 exe 同目录放一个 .env 文件，内容为
         DEEPSEEK_API_KEY=你自己的Key
         DEEPSEEK_BASE_URL=https://api.deepseek.com
         DEEPSEEK_MODEL=deepseek-chat
     模板见 03_源码/.env.example
  3) 源码运行方式：
         python -m pip install -r requirements.txt
         copy .env.example .env   # 填 Key
         python window.py

四、自测
  源码目录下依次运行：
      python tests/test_tools.py     工具层 29 项
      python tests/test_server.py    服务层 18 项
      python tests/test_mcp.py       MCP 13 项
      python tests/test_agent.py     重试与压缩 16 项
  合计 76 项，全部通过，不依赖网络与 API Key。
"@
Set-Content -Path (Join-Path $stage "提交说明.txt") -Value $readme -Encoding utf8

# ---------- 压缩 ----------
Write-Host "`n压缩中..." -ForegroundColor Cyan
$zipOut = Join-Path $root "dist\${folderName}_$stamp.zip"
if (Test-Path $zipOut) { Remove-Item $zipOut -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipOut -CompressionLevel Optimal

$size = [math]::Round((Get-Item $zipOut).Length / 1MB, 1)
Write-Host "`n完成：$zipOut（$size MB）" -ForegroundColor Green
Write-Host "双击检查一遍压缩包内容，就可以交到群里了。" -ForegroundColor Gray
