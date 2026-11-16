param([Parameter(Mandatory=$true)][string]$ScriptPath)
$ErrorActionPreference = 'Stop'
# Run with Windows PowerShell 5.1: GetActiveObject is not exposed by .NET 7+.
$application = [Runtime.InteropServices.Marshal]::GetActiveObject('Photoshop.Application')
$resolved = (Resolve-Path -LiteralPath $ScriptPath).Path
Write-Output ($application.DoJavaScriptFile($resolved))
