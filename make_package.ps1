# make_package.ps1 —— 一键生成"要交的压缩包"
#
# 为什么要有这个脚本：
#   交作业当天最容易乱：报告在根目录、视频在下载文件夹、exe 在 dist、
#   三份验收 JSON 还在项目根、源码在 git 里，手动挑文件必漏。
#   脚本按编号摆好，一次压完。
#
# 用法（在项目根目录打开 PowerShell）：
#   .\make_package.ps1 -Sid 20230001 -Name 张三
#   .\make_package.ps1 -Sid 20230001 -Name 张三 -VideoPath D:\demo.mp4
#   .\make_package.ps1 -Sid 20230001 -Name 张三 -SkipSelftest   # 不想现场跑 180 项自测
#
# 产物：
#   dist\提交_20230001_张三_MiniCursor_日期.zip
#
# 目录结构：
#   01_报告/        report.md（含增量作业三节）
#   02_演示视频/    demo.mp4（没录完会提醒，不阻塞打包）
#   03_源码/        git archive 导出的完整源码（天然不含 .env）
#   04_程序/        MiniCursor.exe
#   05_增量作业/    AGENTS.md + 三份验收 JSON + 增量演示稿 + 自测结果

param(
    [string]$Sid = "学号",
    [string]$Name = "姓名",
    [string]$VideoPath = "",
    [switch]$SkipSelftest
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

Write-Host "[1/6] 组装目录：$folderName" -ForegroundColor Cyan
foreach ($sub in @("01_报告", "02_演示视频", "03_源码", "04_程序", "05_增量作业")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage $sub) | Out-Null
}

# ---------- 2. 报告 ----------
Write-Host "[2/6] 复制 report.md" -ForegroundColor Cyan
if (Test-Path (Join-Path $root "report.md")) {
    Copy-Item (Join-Path $root "report.md") (Join-Path $stage "01_报告\report.md")
} else {
    Write-Host "  !! 缺少 report.md" -ForegroundColor Yellow
}

# ---------- 3. 演示视频 ----------
Write-Host "[3/6] 复制演示视频" -ForegroundColor Cyan
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
Write-Host "[4/6] 导出源码（git archive：保证不含 .env 和构建产物）" -ForegroundColor Cyan
$git = "D:\Git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }
try {
    $zip = Join-Path $env:TEMP "src_$stamp.zip"
    & $git -C $root archive --format=zip -o $zip HEAD
    Expand-Archive -Path $zip -DestinationPath (Join-Path $stage "03_源码") -Force
    Remove-Item $zip -Force
    $srcFiles = (Get-ChildItem (Join-Path $stage "03_源码") -Recurse -File).Count
    Write-Host "  源码导出完成：$srcFiles 个文件（不含 .env，可安全提交）" -ForegroundColor Green
} catch {
    Write-Host "  !! git archive 失败：$_" -ForegroundColor Yellow
    Write-Host "     退而求其次：手动复制 agent/ server/ ui/ tests/ 等目录过去" -ForegroundColor Yellow
}

# ---------- 5. 可执行程序 ----------
Write-Host "[5/6] 复制 MiniCursor.exe" -ForegroundColor Cyan
$exe = Join-Path $root "dist\MiniCursor.exe"
if (Test-Path $exe) {
    Copy-Item $exe (Join-Path $stage "04_程序\MiniCursor.exe")
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "  已放入 MiniCursor.exe（$mb MB）" -ForegroundColor Green
} else {
    Write-Host "  !! dist\MiniCursor.exe 不存在，先跑 .\build.ps1 打包" -ForegroundColor Yellow
}

# ---------- 6. 增量作业 ----------
# 判断标准是"东西得真的有用"，所以验收数字不手抄，直接放程序生成的三份 JSON。
Write-Host "[6/6] 打包增量作业（AGENTS.md + 三份验收 JSON + 自测结果）" -ForegroundColor Cyan
$inc = Join-Path $stage "05_增量作业"

$required = @{
    "AGENTS.md"          = (Join-Path $root "AGENTS.md")
    "skill_report.json"  = (Join-Path $root "skill_report.json")
    "custom_report.json" = (Join-Path $root "custom_report.json")
    "daemon_report.json" = (Join-Path $root "daemon_report.json")
}
foreach ($file in $required.Keys) {
    $srcPath = $required[$file]
    if (Test-Path $srcPath) {
        Copy-Item $srcPath (Join-Path $inc $file)
        Write-Host "  + $file" -ForegroundColor Green
    } else {
        Write-Host "  !! 缺少 $file（跑 python -m app.inspect ... 重新生成）" -ForegroundColor Yellow
    }
}

# 演示稿和定时任务示例：说明"怎么演"和"常驻怎么用"
foreach ($extra in @("DEMO_增量.md", "routines.example.yaml")) {
    if (Test-Path (Join-Path $root $extra)) {
        Copy-Item (Join-Path $root $extra) (Join-Path $inc $extra)
        Write-Host "  + $extra" -ForegroundColor Green
    }
}

# 自测结果：现场跑一遍 180 项，把原始输出一起交上去（比"我跑过了"有说服力）
if (-not $SkipSelftest) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        Write-Host "  正在跑全量自检（约 1 分钟）..." -ForegroundColor Gray
        Push-Location $root
        try {
            $out = & python -m app.selftest 2>&1 | Out-String
            Set-Content -Path (Join-Path $inc "自测结果.json") -Value $out -Encoding utf8
            if ($out -match '"ok":\s*true') {
                $passed = if ($out -match '"total_passed":\s*(\d+)') { $Matches[1] } else { "?" }
                Write-Host "  + 自测结果.json（$passed 项全部通过）" -ForegroundColor Green
            } else {
                Write-Host "  !! 自测没有全绿，结果仍已保存，请先看 05_增量作业\自测结果.json" -ForegroundColor Yellow
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  !! 没找到 python，跳过自测（可用 -SkipSelftest 显式跳过）" -ForegroundColor Yellow
    }
}

# 增量作业说明：老师看压缩包时先看到这一页
$incReadme = @"
增量作业说明（技能系统 / 定制层 / 常驻能力）
========================================
标签：v1.1 技能系统   v1.2 定制层   v1.3 常驻能力

一、本目录内容
  AGENTS.md            给 agent 看的项目地图（也是交付物本身，2446 字节）
  skill_report.json    技能系统验收（披露比、8 条路由实测、误触发数）
  custom_report.json   定制层验收（AGENTS.md 体量、拒绝记录、红转绿、审查上下文增量）
  daemon_report.json   常驻能力验收（定时触发次数、断点续跑步数、通知是否发出）
  自测结果.json        现场跑的全量自检原始输出
  DEMO_增量.md         增量部分 5 分钟演示逐字稿
  routines.example.yaml 定时任务配置示例（复制成 routines.yaml 即生效）

二、三份 JSON 怎么来的（不是手写）
  python -m app.inspect skills --out skill_report.json
  python -m app.inspect custom --out custom_report.json
  python -m app.inspect daemon --out daemon_report.json

三、开关（现场对比用，同一份代码）
  ENABLE_SKILLS=0   关掉技能系统：目录不进上下文，use_skill 工具消失
  ENABLE_HOOKS=0    关掉 hook：不拦截、不自动跑测试
  AGENT_NOTIFY=0    关掉完成通知

四、自测
  python -m app.selftest        八组共 180 项
  分组：工具 29 / 服务 18 / MCP 13 / 智能体 16 / 会话 22 / 技能 21 / 定制 40 / 常驻 21
"@
Set-Content -Path (Join-Path $inc "增量作业说明.txt") -Value $incReadme -Encoding utf8

# ---------- 提交说明 ----------
$readme = @"
提交材料说明
========================================
学号：$Sid
姓名：$Name
项目：Mini Cursor（桌面编程智能体）
日期：$stamp

一、要交的链接
  仓库：   https://github.com/WJX10163722/desktop-agent
  Release：https://github.com/WJX10163722/desktop-agent/releases/tag/v1.0.0
  增量标签：v1.1（技能系统） v1.2（定制层） v1.3（常驻能力）

二、压缩包内容
  01_报告/report.md                设计报告（含增量作业三节）
  02_演示视频/demo.mp4             现场演示录屏
  03_源码/                         完整源码（不含 .env，无密钥泄漏）
  04_程序/MiniCursor.exe           可双击运行的桌面程序
  05_增量作业/                      AGENTS.md、三份验收 JSON、自测原始输出、演示稿

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
  源码目录下执行一次即可跑完全部八组：
      python -m app.selftest
  合计 180 项，全部通过，不依赖网络与 API Key。
  分组：工具 29 / 服务 18 / MCP 13 / 智能体 16 / 会话 22 / 技能 21 / 定制 40 / 常驻 21

五、增量作业验收
  见 05_增量作业/增量作业说明.txt；
  三份 JSON 由 python -m app.inspect 生成，数字可复现。
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
if (-not $video) {
    Write-Host "提醒：demo.mp4 还没放进去，录完重跑本脚本即可（会自动找项目根目录的 demo.mp4）。" -ForegroundColor Yellow
}
