param([switch]$Validation)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$source=Join-Path $PSScriptRoot 'uxp'
$manifest=Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$suffix=if($Validation){'-validation'}else{''}
$output=Join-Path $PSScriptRoot ("build\Digital8Panel-$($manifest.version)$suffix.ccx")
if(Test-Path -LiteralPath $output){Remove-Item -LiteralPath $output}
$archive=[IO.Compression.ZipFile]::Open($output,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach($file in Get-ChildItem -LiteralPath $source -File) {
        if($Validation -and $file.Name -eq 'main.js') {
            $main=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
            $main += "`nsetTimeout(() => require('./self-test.js').run(require('photoshop')).catch(console.error), 300);`n"
            $entry=$archive.CreateEntry('main.js')
            $writer=New-Object IO.StreamWriter($entry.Open(),(New-Object Text.UTF8Encoding($false)))
            try{$writer.Write($main)}finally{$writer.Dispose()}
        } else { [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$file.FullName,$file.Name,[IO.Compression.CompressionLevel]::Optimal) | Out-Null }
    }
    if($Validation) {
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,(Join-Path $PSScriptRoot 'validation\uxp_self_test.js'),'self-test.js',[IO.Compression.CompressionLevel]::Optimal) | Out-Null
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,(Join-Path $PSScriptRoot 'build\photoshop-smoke\input.png'),'fixture.png',[IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally { $archive.Dispose() }
Write-Output $output
if ($Validation) { return }
$binary=Join-Path $PSScriptRoot 'build\plugin\Digital8.8bf'
if (!(Test-Path -LiteralPath $binary)) { throw 'Build Digital8.8bf before packaging the Windows bundle.' }
$bundleName="Digital8Photoshop-$($manifest.version)-win64"
$bundle=Join-Path $PSScriptRoot ("build\"+$bundleName)
New-Item -ItemType Directory -Path $bundle -Force | Out-Null
$panelName=Split-Path $output -Leaf
Copy-Item -LiteralPath $output -Destination (Join-Path $bundle $panelName) -Force
Copy-Item -LiteralPath $binary -Destination (Join-Path $bundle 'Digital8.8bf') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $bundle 'README_ja.md') -Force
Copy-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'LICENSE') -Destination (Join-Path $bundle 'LICENSE') -Force
$files=@((Join-Path $bundle $panelName),(Join-Path $bundle 'Digital8.8bf'),(Join-Path $bundle 'README_ja.md'),(Join-Path $bundle 'LICENSE'))
$hashes=($files | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+(Split-Path $_ -Leaf) }) -join "`n"
$hashFile=Join-Path $bundle 'SHA256.txt'
[IO.File]::WriteAllText($hashFile,$hashes+"`n",[Text.Encoding]::ASCII)
$archivePath=Join-Path $PSScriptRoot ("build\"+$bundleName+'.zip')
Compress-Archive -LiteralPath ($files+@($hashFile)) -DestinationPath $archivePath -Force
Write-Output $archivePath
