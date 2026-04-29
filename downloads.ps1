$dl = Join-Path $PSScriptRoot "downloads"
New-Item -ItemType Directory -Force -Path $dl | Out-Null
Set-Location $dl

$files = @(
    @{ name = "toolchain-xtensa-esp32-windows_amd64-8.4.0+2021r2-patch5.tar.gz";             url = "https://dl.registry.platformio.org/download/espressif/tool/toolchain-xtensa-esp32/8.4.0+2021r2-patch5/toolchain-xtensa-esp32-windows_amd64-8.4.0+2021r2-patch5.tar.gz" },
    @{ name = "toolchain-xtensa-esp32s3-windows_amd64-8.4.0+2021r2-patch5.tar.gz";           url = "https://dl.registry.platformio.org/download/espressif/tool/toolchain-xtensa-esp32s3/8.4.0+2021r2-patch5/toolchain-xtensa-esp32s3-windows_amd64-8.4.0+2021r2-patch5.tar.gz" },
    @{ name = "framework-arduinoespressif32-3.20017.241212+sha.dcc1105b.tar.gz";             url = "https://dl.registry.platformio.org/download/platformio/tool/framework-arduinoespressif32/3.20017.241212+sha.dcc1105b/framework-arduinoespressif32-3.20017.241212+sha.dcc1105b.tar.gz" },
    @{ name = "tool-esptoolpy-2.41100.0.tar.gz";                                             url = "https://dl.registry.platformio.org/download/platformio/tool/tool-esptoolpy/2.41100.0/tool-esptoolpy-2.41100.0.tar.gz" },
    @{ name = "tool-mklittlefs-windows_amd64-1.203.210628.tar.gz";                           url = "https://dl.registry.platformio.org/download/platformio/tool/tool-mklittlefs/1.203.210628/tool-mklittlefs-windows_amd64-1.203.210628.tar.gz" },
    @{ name = "tool-mkspiffs-windows-2.230.0.tar.gz";                                        url = "https://dl.registry.platformio.org/download/platformio/tool/tool-mkspiffs/2.230.0/tool-mkspiffs-windows-2.230.0.tar.gz" }
)

foreach ($f in $files) {
    $target = Join-Path $dl $f.name
    if ((Test-Path $target) -and ((Get-Item $target).Length -gt 0)) {
        Write-Host "[skip] already have $($f.name)" -ForegroundColor Yellow
        continue
    }
    Write-Host "`n[download] $($f.name)" -ForegroundColor Cyan
    curl.exe -L --retry 3 -o $target $f.url
    if ($LASTEXITCODE -ne 0) { Write-Host "!! download failed for $($f.name)" -ForegroundColor Red }
}

Write-Host "`n==== Downloaded files ====" -ForegroundColor Green
Get-ChildItem $dl -Filter *.tar.gz | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}} | Format-Table -AutoSize
