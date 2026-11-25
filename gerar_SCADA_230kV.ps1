param(
    [string]$MmsExePath = ""
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonFile = Join-Path $Root 'scada_subestacao_230kv.py'

if (-not (Test-Path $PythonFile)) {
    throw "Arquivo Python não encontrado: $PythonFile"
}

if ([string]::IsNullOrWhiteSpace($MmsExePath)) {
    $Candidates = @(
        (Join-Path $Root 'iec61850_mms_server.exe'),
        (Join-Path (Join-Path $Root 'build_mms') 'iec61850_mms_server.exe')
    )
    $MmsExePath = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
elseif (-not [System.IO.Path]::IsPathRooted($MmsExePath)) {
    $MmsExePath = Join-Path $Root $MmsExePath
}

if (-not $MmsExePath -or -not (Test-Path $MmsExePath)) {
    throw "Servidor MMS não encontrado. Coloque iec61850_mms_server.exe na mesma pasta deste script ou em .\build_mms\, ou informe -MmsExePath."
}

$MmsExePath = (Resolve-Path $MmsExePath).Path
$MmsDir = Split-Path -Parent $MmsExePath

py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Falha ao atualizar pip.' }
py -m pip install pyinstaller customtkinter matplotlib scapy
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências Python.' }

$BuildArgs = @(
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', 'SCADA_230kV',
    '--collect-all', 'customtkinter',
    '--collect-all', 'matplotlib',
    '--collect-all', 'scapy',
    '--add-binary', "$MmsExePath;."
)

Get-ChildItem -Path $MmsDir -Filter '*.dll' -File -ErrorAction SilentlyContinue | ForEach-Object {
    $BuildArgs += @('--add-binary', "$($_.FullName);.")
}

$BuildArgs += $PythonFile
py -m PyInstaller @BuildArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller não conseguiu gerar o executável.' }

$Output = Join-Path $Root 'dist\SCADA_230kV.exe'
if (-not (Test-Path $Output)) { throw 'O executável final não foi encontrado na pasta dist.' }
Write-Host "Concluído: $Output" -ForegroundColor Green