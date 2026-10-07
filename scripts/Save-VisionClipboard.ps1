# Run with Windows PowerShell 5.1: powershell.exe -STA -File ...
param([Parameter(Mandatory=$true)][string]$Share)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if (-not [System.Windows.Forms.Clipboard]::ContainsImage()) {
    throw 'Clipboard contains no image. Capture with Win+Shift+S first.'
}
if (-not (Test-Path -LiteralPath $Share -PathType Container)) {
    throw 'The dedicated NAS image share does not exist.'
}
$name = 'capture-' + [guid]::NewGuid().ToString('N') + '.png'
$destination = Join-Path $Share $name
$image = [System.Windows.Forms.Clipboard]::GetImage()
try {
    $image.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $image.Dispose()
}
Write-Output ('Strata: analyze_image image="' + $name + '" detail="high"')
