param([string]$Compiler = '')
$ErrorActionPreference = 'Stop'
if (!$Compiler) {
    $found = Get-Command cl.exe -ErrorAction SilentlyContinue
    if ($found) { $Compiler = $found.Source }
    else {
        $candidates = @(
            "$env:ProgramFiles\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe",
            "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"
        )
        $Compiler = Get-Item $candidates -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (!$Compiler) { throw 'MSVC x64 cl.exe was not found. Pass -Compiler with its absolute path.' }
$build = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $build | Out-Null
Push-Location $build
try {
    # This allocation-free kernel needs no CRT, Windows SDK or Photoshop SDK.
    # Strict floating-point evaluation preserves the Python threshold decisions.
    & $Compiler /nologo /LD /O2 /std:c++17 /fp:strict /GS- /GR- /Zl /W4 /WX `
        (Join-Path $PSScriptRoot 'native\digital8_core.cpp') `
        /link /NODEFAULTLIB /NOENTRY /MACHINE:X64 /OUT:digital8_core.dll
    if ($LASTEXITCODE -ne 0) { throw "Native build failed ($LASTEXITCODE)." }
} finally { Pop-Location }
Write-Output (Join-Path $build 'digital8_core.dll')
