# Copies customs\assets\ into the client: ro3emu\custom (models, icons, items.tsv) and ro3emu\hats (hat models).
# Usage: .\customs\scripts\Deploy-Customs.ps1 [-ClientDir C:\Games\Ragnarok3-Official] [-WhatIf]
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$ClientDir = 'C:\Games\Ragnarok3-Official')
$assets = Join-Path $PSScriptRoot '..\assets'
$custom = Join-Path $ClientDir 'ro3emu\custom'
if (-not (Test-Path (Join-Path $ClientDir 'ro3.exe'))) { throw "No ro3.exe in $ClientDir" }
$map = @(
    @{ From = 'models'; To = $custom },
    @{ From = 'icons'; To = $custom },
    @{ From = 'hats'; To = (Join-Path $ClientDir 'ro3emu') }
)
foreach ($m in $map) {
    $from = Join-Path $assets $m.From
    if (-not (Test-Path $from)) { continue }
    $to = Join-Path $m.To $m.From
    if ($PSCmdlet.ShouldProcess($to, "copy $($m.From)")) {
        New-Item -ItemType Directory -Force $to | Out-Null
        Copy-Item (Join-Path $from '*') $to -Recurse -Force
    }
}
$items = Join-Path $assets 'items.tsv'
if ((Test-Path $items) -and $PSCmdlet.ShouldProcess($custom, 'copy items.tsv')) {
    New-Item -ItemType Directory -Force $custom | Out-Null
    Copy-Item $items $custom -Force
}
Write-Host "Done. Remove $custom (and ro3emu\hats) to switch every custom asset off."
