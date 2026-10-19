param(
    [string]$StageDirectory = (Join-Path $env:TEMP 'ige-csi-rx-idf601')
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'csi_recv'
$output = Join-Path $repo 'prebuilt/rx'

function Assert-PackageFlashLayout {
    param([object]$Flasher, [string]$FlashArguments)
    $settings = [ordered]@{ flash_mode = 'dio'; flash_freq = '80m'; flash_size = '2MB' }
    $files = [ordered]@{
        '0x0' = 'bootloader/bootloader.bin'
        '0xa000' = 'partition_table/partition-table.bin'
        '0x20000' = 'csi_recv.bin'
    }
    foreach ($name in $settings.Keys) {
        if ($Flasher.flash_settings.$name -cne $settings[$name]) {
            throw "Generated flash setting $name does not match flash-rx.ps1 ($($settings[$name])). Use a fresh staging directory."
        }
    }
    if (@($Flasher.flash_files.PSObject.Properties).Count -ne $files.Count) {
        throw 'Generated flash file list must contain only the bootloader, partition table, and csi_recv app.'
    }
    foreach ($offset in $files.Keys) {
        if ($Flasher.flash_files.PSObject.Properties[$offset].Value -cne $files[$offset]) {
            throw "Generated flash mapping at $offset differs from flash-rx.ps1. Use a fresh staging directory."
        }
    }
    if ($Flasher.extra_esptool_args.chip -cne 'esp32s3') {
        throw 'Generated flasher manifest does not target ESP32-S3.'
    }
    foreach ($imageName in @('bootloader', 'partition-table', 'app')) {
        if ([string]$Flasher.$imageName.encrypted -ne 'false') {
            throw 'Encrypted images are not supported by this prebuilt flash script.'
        }
    }
    $expectedOptions = @{ '--flash-mode' = 'dio'; '--flash-freq' = '80m'; '--flash-size' = '2MB' }
    $options = @($Flasher.write_flash_args)
    if ($options.Count -ne 6) { throw 'Generated write_flash_args contains unsupported options.' }
    for ($index = 0; $index -lt $options.Count; $index += 2) {
        $option = [string]$options[$index]
        if (-not $expectedOptions.ContainsKey($option) -or $options[$index + 1] -cne $expectedOptions[$option]) {
            throw "Generated write_flash_args differs from flash-rx.ps1: $option"
        }
        $expectedOptions.Remove($option)
    }
    # IDF 6.0.1's argument file is also shipped as the direct esptool fallback.
    $expectedText = '--flash-mode dio --flash-freq 80m --flash-size 2MB 0x0 bootloader/bootloader.bin 0xa000 partition_table/partition-table.bin 0x20000 csi_recv.bin'
    $actualText = ($FlashArguments.Trim() -split '\s+') -join ' '
    if ($actualText -cne $expectedText) {
        throw 'Generated flash_args differs from the validated flash-rx.ps1 layout/settings. No package was updated.'
    }
}

if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    throw 'Run this build script in an ESP-IDF 6.0.1 PowerShell environment.'
}
$idfVersion = & idf.py --version
if ($LASTEXITCODE -ne 0 -or "$idfVersion" -notmatch 'v6\.0\.1(?:\s|$)') {
    throw "ESP-IDF v6.0.1 is required; found: $idfVersion"
}
New-Item -ItemType Directory -Force -Path $StageDirectory | Out-Null
$stage = (Resolve-Path -LiteralPath $StageDirectory).Path
if ($stage -match '[^\x00-\x7F]') { throw 'Use an ASCII-only staging path, for example C:\temp\ige-csi-rx.' }
Copy-Item -LiteralPath "$source\CMakeLists.txt", "$source\sdkconfig.defaults" -Destination $stage -Force
Copy-Item -LiteralPath "$source\main" -Destination $stage -Recurse -Force
if (Test-Path -LiteralPath "$source\dependencies.lock") {
    Copy-Item -LiteralPath "$source\dependencies.lock" -Destination $stage -Force
}
$sourceNames = @('CMakeLists.txt', 'sdkconfig.defaults', 'main/CMakeLists.txt', 'main/app_main.c', 'main/rx_csi_format.h', 'main/csi_control_protocol.h', 'main/idf_component.yml')
# Build the same canonical LF bytes recorded in Git, including on Windows.
foreach ($name in $sourceNames) {
    $path = Join-Path $stage $name
    $text = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($path, $text, [Text.UTF8Encoding]::new($false))
}
Push-Location $stage
try {
    if (-not (Test-Path -LiteralPath 'sdkconfig')) {
        & idf.py set-target esp32s3
        if ($LASTEXITCODE -ne 0) { throw 'Setting ESP32-S3 target failed.' }
    }
    & idf.py build
    if ($LASTEXITCODE -ne 0) { throw 'Firmware build failed.' }
    # This package and flash-rx.ps1 use the existing team's partition layout.
    $config = Get-Content -LiteralPath 'sdkconfig' -Raw
    foreach ($required in @('CONFIG_IDF_TARGET="esp32s3"', 'CONFIG_PARTITION_TABLE_OFFSET=0xa000', 'CONFIG_ESP_CONSOLE_UART_BAUDRATE=921600', 'CONFIG_FREERTOS_HZ=100', 'CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=128', '# CONFIG_ESP_WIFI_AMPDU_TX_ENABLED is not set')) {
        if ($config -notmatch ('(?m)^' + [regex]::Escape($required) + '\r?$')) {
            throw "Cached sdkconfig differs from package requirements: $required. Use a fresh staging directory."
        }
    }
    foreach ($requiredFile in @('build\flasher_args.json', 'build\flash_args')) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Firmware build did not produce $requiredFile. No package was updated."
        }
    }
    $flasher = Get-Content -LiteralPath 'build\flasher_args.json' -Raw | ConvertFrom-Json
    Assert-PackageFlashLayout -Flasher $flasher -FlashArguments (Get-Content -LiteralPath 'build\flash_args' -Raw)
    $dependencyLock = [IO.File]::ReadAllText((Join-Path $stage 'dependencies.lock')).Replace("`r`n", "`n")
    [IO.File]::WriteAllText((Join-Path $stage 'dependencies.lock'), $dependencyLock, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $source 'dependencies.lock'), $dependencyLock, [Text.UTF8Encoding]::new($false))
    $sourceNames += 'dependencies.lock'
    New-Item -ItemType Directory -Force -Path "$output\bootloader", "$output\partition_table" | Out-Null
    Copy-Item -LiteralPath 'build\csi_recv.bin', 'build\flash_args', 'build\flasher_args.json' -Destination $output -Force
    Copy-Item -LiteralPath 'sdkconfig' -Destination "$output\sdkconfig.build" -Force
    Copy-Item -LiteralPath 'build\bootloader\bootloader.bin' -Destination "$output\bootloader" -Force
    Copy-Item -LiteralPath 'build\partition_table\partition-table.bin' -Destination "$output\partition_table" -Force
    $binaryNames = @('bootloader/bootloader.bin', 'partition_table/partition-table.bin', 'csi_recv.bin')
    $hashLines = foreach ($name in $binaryNames) {
        (Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $name
    }
    Set-Content -LiteralPath "$output\SHA256SUMS" -Value $hashLines -Encoding ascii
    $sourceHashes = foreach ($name in $sourceNames) {
        [ordered]@{ path = "csi_recv/$name"; sha256 = (Get-FileHash -LiteralPath (Join-Path $stage $name) -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    $description = Get-Content -LiteralPath 'build\project_description.json' -Raw | ConvertFrom-Json
    $app = Get-Item -LiteralPath "$output\csi_recv.bin"
    [ordered]@{
        firmware_version = $description.project_version
        output_format = 'CSI_DATA_B64'; field_count = 28; csi_encoding = 'RFC4648 base64 of raw signed-int8 bytes'
        idf_version = "$idfVersion"; target = 'esp32s3'; source_files = @($sourceHashes)
        binary_files = $binaryNames; flash_settings = $flasher.flash_settings; flash_files = $flasher.flash_files
        app_sha256 = (Get-FileHash -LiteralPath $app.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        app_bytes = $app.Length
        source_hash_note = 'Build inputs and source hashes use canonical UTF-8 LF bytes, matching the repository files.'
        hardware_validation = 'This binary has not been flashed or hardware-tested. RX Base64 reception, queue overflow, and collector compatibility require hardware measurement. Earlier fixed TX/RX results belong to a different binary.'
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$output\build-info.json" -Encoding utf8
    Write-Output "Firmware package updated: $output"
} finally { Pop-Location }
