# One-off: gathers our custom hats from the old test client (EclipseClient) into customs\assets, so they are kept in one place.
# The hat files there were parked as ro3emu\custom.off (aliases, icons, items.tsv) and ro3emu\hats.off (hat models).
# Usage: .\customs\scripts\Import-From-EclipseClient.ps1 [-From C:\Games\EclipseClient] [-WhatIf]
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$From = 'C:\Games\EclipseClient')
$assets = Join-Path $PSScriptRoot '..\assets'
$src = Join-Path $From 'ro3emu'
function Get-Files($folder) { Get-ChildItem (Join-Path $src $folder) -File | Where-Object { $_.Name -notmatch ' ' } }
$plan = @(
    @{ Folder = 'custom.off\models'; To = 'models' },
    @{ Folder = 'custom.off\icons'; To = 'icons' },
    @{ Folder = 'hats.off'; To = 'hats' }
)
foreach ($p in $plan) {
    $to = Join-Path $assets $p.To
    if ($PSCmdlet.ShouldProcess($to, "copy $($p.Folder)")) {
        New-Item -ItemType Directory -Force $to | Out-Null
        $files = Get-Files $p.Folder
        $files | Copy-Item -Destination $to -Force
        Write-Host ("{0}: {1} files" -f $p.To, @($files).Count)
    }
}
if ($PSCmdlet.ShouldProcess($assets, 'copy items.tsv')) {
    Copy-Item (Join-Path $src 'custom.off\items.tsv') $assets -Force
}
Write-Host 'Done. Files with a space in the name were skipped (old duplicates).'
