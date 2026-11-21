<#
  把仓库里的 skill 源码安装 / 更新到本机全局 skill 目录。

  用法（在仓库根目录）：
    powershell -ExecutionPolicy Bypass -File skills\install.ps1
    powershell -ExecutionPolicy Bypass -File skills\install.ps1 -Mode copy

  说明：
  - 默认（-Mode auto）优先创建符号链接：改仓库源码立即生效，无需重复同步。
  - Windows 未开启开发者模式、或当前用户无创建符号链接权限时会自动回退为复制，
    并打印回退原因；-Mode copy 可强制走复制。
  - 复制模式下改完源码，需要重新执行本脚本才会生效。
  - 目标目录已存在时会被覆盖，保证全局副本与仓库一致。
#>

param(
    [string]$Name = "sdz-ptah-avatar",
    [ValidateSet("auto", "link", "copy")]
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"

$src = Join-Path $PSScriptRoot $Name
if (-not (Test-Path (Join-Path $src "SKILL.md"))) {
    throw "找不到 skill 源码：$src"
}

$root = Join-Path $env:USERPROFILE ".trae-cn\skills"
New-Item -ItemType Directory -Force -Path $root | Out-Null
$dst = Join-Path $root $Name
$staging = Join-Path $root ("." + $Name + ".staging")

if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
Copy-Item -Recurse -Force $src $staging

$linked = $false
if ($Mode -ne "copy") {
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    try {
        New-Item -ItemType SymbolicLink -Path $dst -Target $src -ErrorAction Stop | Out-Null
        $linked = $true
    } catch {
        Write-Host "创建符号链接失败，回退为复制：$($_.Exception.Message)"
    }
}

if (-not $linked) {
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Move-Item -Path $staging -Destination $dst
} else {
    Remove-Item -Recurse -Force $staging
}

if (-not (Test-Path (Join-Path $dst "SKILL.md"))) {
    throw "安装失败：$dst 下没有 SKILL.md"
}

if ($linked) {
    Write-Host "已安装（符号链接）：$dst -> $src"
    Write-Host "修改仓库源码后立即生效，无需再次同步。"
} else {
    Write-Host "已安装（复制）：$dst"
    Write-Host "修改仓库源码后需重新执行本脚本才会生效。"
}
