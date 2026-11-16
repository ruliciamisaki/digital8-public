param([string]$SdkRoot = '', [string]$MSBuild = '', [string]$PlatformToolset = 'v145')
$ErrorActionPreference = 'Stop'
if (!$SdkRoot) { $SdkRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'adobe_photoshop_sdk_2027_win_v1.0' }
$SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path
if (!$MSBuild) {
    $MSBuild = Get-Item "$env:ProgramFiles\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (!$MSBuild) { throw 'MSBuild.exe not found.' }
foreach ($name in @('NativeCli','HostTests','NativePreview')) {
    & $MSBuild (Join-Path $PSScriptRoot "validation\$name.vcxproj") /t:Build /p:Configuration=Release /p:Platform=x64 "/p:PlatformToolset=$PlatformToolset" "/p:Digital8SdkRoot=$SdkRoot" /nologo /verbosity:minimal
    if ($LASTEXITCODE -ne 0) { throw "$name build failed." }
}
& (Join-Path $PSScriptRoot 'build\validation\host_tests.exe') (Join-Path $PSScriptRoot 'build\plugin\Digital8.8bf')
if ($LASTEXITCODE -ne 0) { throw 'Host integration tests failed.' }
