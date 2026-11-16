param([string]$SdkRoot = '', [string]$MSBuild = '', [string]$PlatformToolset = '')
$ErrorActionPreference = 'Stop'
if (!$SdkRoot) {
    $localSdk = Join-Path (Split-Path $PSScriptRoot -Parent) 'adobe_photoshop_sdk_2027_win_v1.0'
    $SdkRoot = if (Test-Path -LiteralPath $localSdk) { $localSdk } else {
        Join-Path $PSScriptRoot 'third_party\photoshop-cpp-sdk'
    }
}
$SdkRoot = (Resolve-Path -LiteralPath $SdkRoot).Path
$project = Join-Path $SdkRoot 'pluginsdk\samplecode\filter\dissolve\win\Dissolve.vcxproj'
$header = Join-Path $SdkRoot 'pluginsdk\photoshopapi\photoshop\PIFilter.h'
if (!(Test-Path -LiteralPath $project) -or !(Test-Path -LiteralPath $header)) {
    throw "Photoshop SDK headers and Dissolve sample not found in $SdkRoot"
}
if (!$MSBuild) {
    $command = Get-Command MSBuild.exe -ErrorAction SilentlyContinue
    if ($command) { $MSBuild = $command.Source }
    else {
        $patterns = @(
            "$env:ProgramFiles\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe",
            "${env:ProgramFiles(x86)}\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe"
        )
        $MSBuild = Get-Item $patterns -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if (!$MSBuild) { throw 'MSBuild.exe not found. Install the Visual C++ build tools or pass -MSBuild.' }
if (!$PlatformToolset) {
    $vsRoot = $MSBuild
    for ($i = 0; $i -lt 4; $i++) { $vsRoot = Split-Path $vsRoot -Parent }
    $toolsets = @(Get-ChildItem (Join-Path $vsRoot 'MSBuild\Microsoft\VC\*\Platforms\x64\PlatformToolsets\*') `
        -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name -Unique)
    if ($toolsets -contains 'v143') { $PlatformToolset = 'v143' }
    elseif ($toolsets.Count) { $PlatformToolset = $toolsets | Sort-Object -Descending | Select-Object -First 1 }
}
# Build an isolated copy so the supplied SDK stays untouched. The sample is
# Win32 (UseOfMfc=false); only its generated .rc file includes the MFC wrapper.
$stage = Join-Path $PSScriptRoot 'build\sdk-sample'
$api = Join-Path $stage 'pluginsdk'
$samples = Join-Path $api 'samplecode'
$filters = Join-Path $samples 'filter'
New-Item -ItemType Directory -Force -Path $api, $samples, $filters | Out-Null
Copy-Item -LiteralPath (Join-Path $SdkRoot 'pluginsdk\photoshopapi') -Destination $api -Recurse -Force
Copy-Item -LiteralPath (Join-Path $SdkRoot 'pluginsdk\samplecode\common') -Destination $samples -Recurse -Force
Copy-Item -LiteralPath (Join-Path $SdkRoot 'pluginsdk\samplecode\filter\dissolve') -Destination $filters -Recurse -Force
$project = Join-Path $filters 'dissolve\win\Dissolve.vcxproj'
$resource = Join-Path $filters 'dissolve\win\Dissolve.rc'
$encoding = [System.Text.Encoding]::GetEncoding(1252)
$rcText = [System.IO.File]::ReadAllText($resource, $encoding)
$rcText = $rcText.Replace('#include "afxres.h"', "#include <winres.h>`r`n#ifndef IDC_STATIC`r`n#define IDC_STATIC (-1)`r`n#endif")
[System.IO.File]::WriteAllText($resource, $rcText, $encoding)
$arguments = @($project, '/t:Build', '/p:Configuration=Release', '/p:Platform=x64', '/nologo', '/verbosity:minimal')
if ($PlatformToolset) { $arguments += "/p:PlatformToolset=$PlatformToolset" }
Write-Output "SDK: $SdkRoot"
Write-Output "Toolset: $PlatformToolset; staged project: $project"
$previousCl = $env:CL
try {
    # New geometry headers use std::numeric_limits<T>::max().
    $env:CL = "$previousCl /DNOMINMAX"
    & $MSBuild @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Dissolve sample build failed ($LASTEXITCODE). See the MSBuild error above for the missing component or source error."
    }
} finally {
    $env:CL = $previousCl
}
$output = Join-Path $samples 'Output\x64\Release\Dissolve.8bf'
if (!(Test-Path -LiteralPath $output)) { throw "Build succeeded but the expected plug-in was not found: $output" }
Write-Output $output
