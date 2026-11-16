param([string]$SdkRoot = '', [string]$MSBuild = '', [string]$PlatformToolset = '')
$ErrorActionPreference = 'Stop'
if (!$SdkRoot) { $SdkRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'adobe_photoshop_sdk_2027_win_v1.0' }
$SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path
if (!(Test-Path -LiteralPath (Join-Path $SdkRoot 'pluginsdk\photoshopapi\photoshop\PIFilter.h'))) { throw 'Photoshop C++ SDK not found.' }
if (!$MSBuild) {
    $MSBuild = Get-Item "$env:ProgramFiles\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (!$MSBuild) { throw 'MSBuild.exe not found. Pass -MSBuild.' }
if (!$PlatformToolset) {
    $vsRoot = $MSBuild
    for ($i=0; $i -lt 4; $i++) { $vsRoot = Split-Path $vsRoot -Parent }
    $PlatformToolset = Get-ChildItem (Join-Path $vsRoot 'MSBuild\Microsoft\VC\*\Platforms\x64\PlatformToolsets\*') -Directory |
        Sort-Object { [int]($_.Name -replace '\D','') } -Descending | Select-Object -First 1 -ExpandProperty Name
}
if (!$PlatformToolset) { throw 'An x64 Visual C++ platform toolset is required.' }
& $MSBuild (Join-Path $PSScriptRoot 'plugin\Digital8.vcxproj') /t:Build /p:Configuration=Release /p:Platform=x64 "/p:PlatformToolset=$PlatformToolset" "/p:Digital8SdkRoot=$SdkRoot" /nologo /verbosity:minimal
if ($LASTEXITCODE -ne 0) { throw "Digital8 filter build failed ($LASTEXITCODE)." }
$output = Join-Path $PSScriptRoot 'build\plugin\Digital8.8bf'
if (!(Test-Path -LiteralPath $output)) { throw 'Digital8.8bf was not generated.' }
Write-Output $output
