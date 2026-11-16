$ErrorActionPreference='Stop'
python (Join-Path $PSScriptRoot 'prepare_photoshop_smoke.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare Photoshop test inputs.' }
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot 'invoke_photoshop.ps1') -ScriptPath (Join-Path $PSScriptRoot 'photoshop_smoke.jsx')
if ($LASTEXITCODE -ne 0) { throw 'Photoshop script invocation failed.' }
python (Join-Path $PSScriptRoot 'verify_photoshop_smoke.py')
if ($LASTEXITCODE -ne 0) { throw 'Photoshop output verification failed.' }
