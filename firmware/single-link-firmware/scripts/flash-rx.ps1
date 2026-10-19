param(
    [Parameter(Mandatory = $true)][string]$Port,
    [ValidateSet(115200, 230400, 460800, 921600)][int]$Baud = 460800,
    [string]$Python = '',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$package = Join-Path (Split-Path -Parent $PSScriptRoot) 'prebuilt/rx'
if (-not (Test-Path -LiteralPath $package -PathType Container)) {
    throw 'Missing prebuilt/rx directory. Download the complete firmware package or run scripts/build-rx.ps1 first.'
}
if (-not (Test-Path -LiteralPath "$package\SHA256SUMS" -PathType Leaf)) {
    throw 'Missing prebuilt/rx/SHA256SUMS. Download the complete firmware package or run scripts/build-rx.ps1 first.'
}
if (-not $Python) {
    if ($env:IDF_PYTHON_ENV_PATH -and (Test-Path -LiteralPath "$env:IDF_PYTHON_ENV_PATH\Scripts\python.exe")) {
        $Python = "$env:IDF_PYTHON_ENV_PATH\Scripts\python.exe"
    } else { $Python = 'python' }
}
$expected = @('bootloader/bootloader.bin', 'partition_table/partition-table.bin', 'csi_recv.bin')
foreach ($name in $expected) {
    if (-not (Test-Path -LiteralPath (Join-Path $package $name) -PathType Leaf)) {
        throw "Missing prebuilt/rx/$name. Download the complete firmware package or run scripts/build-rx.ps1 first."
    }
}
$seen = @()
foreach ($line in Get-Content -LiteralPath "$package\SHA256SUMS") {
    if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw 'Invalid SHA256SUMS entry.' }
    $hash = $Matches[1]
    $name = $Matches[2]
    if ($name -notin $expected -or $name -in $seen) { throw "Unexpected or duplicate binary: $name" }
    $actual = (Get-FileHash -LiteralPath (Join-Path $package $name) -Algorithm SHA256).Hash
    if ($actual -ne $hash) { throw "Binary checksum mismatch: $name" }
    $seen += $name
}
if ($seen.Count -ne $expected.Count) { throw 'Incomplete firmware package.' }
$arguments = @('-m', 'esptool', '--chip', 'esp32s3', '-p', $Port, '-b', "$Baud",
    '--before', 'default-reset', '--after', 'hard-reset', 'write-flash',
    '--flash-mode', 'dio', '--flash-freq', '80m', '--flash-size', '2MB',
    '0x0', "$package\bootloader\bootloader.bin",
    '0xa000', "$package\partition_table\partition-table.bin",
    '0x20000', "$package\csi_recv.bin")
Write-Output "ESP32-S3 / $Port / $Baud baud; binary checksums verified."
if ($DryRun) {
    Write-Output 'Dry run: no device connection or writes.'
    Write-Output $Python
    Write-Output $arguments
    return
}
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Flash failed. Close other serial programs, check the port/cable, and retry at -Baud 115200 if needed.' }
Write-Output 'Flash complete. UART: 921600 baud. Fixed RX starts receiving immediately. The collector must support CSI_DATA_B64 before capturing.'
