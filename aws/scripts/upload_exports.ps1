# Upload the current Power BI exports as one immutable ingestion batch, manifest LAST (Windows PowerShell).
#   $env:GL_BUCKET = "<bucket>"; .\aws\scripts\upload_exports.ps1 [-RunDate YYYY-MM-DD] [-BatchId ID] [-Source DIR]
# Requires: AWS CLI v2 with a signed-in profile (aws sso login), the project venv active.
# Never uses --delete: earlier batches are never removed or overwritten.
param([string]$RunDate, [string]$BatchId, [string]$Source)
$ErrorActionPreference = "Stop"
if (-not $env:GL_BUCKET) { throw "Set `$env:GL_BUCKET to your bucket name." }
$Region = if ($env:GL_REGION) { $env:GL_REGION } else { "ap-south-1" }
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$pyArgs = @("$Root\aws\scripts\build_batch.py", "--print-dir")
if ($RunDate) { $pyArgs += @("--run-date", $RunDate) }
if ($BatchId) { $pyArgs += @("--batch-id", $BatchId) }
if ($Source)  { $pyArgs += @("--source", $Source) }
$BatchDir = (& python @pyArgs)
if ($LASTEXITCODE -ne 0) { throw "build_batch.py failed" }
$BatchDir = $BatchDir.Trim()
$RunPart = Split-Path (Split-Path $BatchDir -Parent) -Leaf
$BatchPart = Split-Path $BatchDir -Leaf
$Key = "raw/powerbi/$RunPart/$BatchPart"
$Dest = "s3://$($env:GL_BUCKET)/$Key/"

aws s3api head-object --bucket $env:GL_BUCKET --key "$Key/_manifest.json" --region $Region *> $null
if ($LASTEXITCODE -eq 0) { throw "$Dest already has a manifest; batches are immutable (choose a new -BatchId)." }

Write-Host "Uploading CSVs to $Dest"
aws s3 sync $BatchDir $Dest --exclude "_manifest.json" --only-show-errors --region $Region
if ($LASTEXITCODE -ne 0) { throw "aws s3 sync failed" }
Write-Host "Uploading the manifest last (this starts the pipeline)"
aws s3 cp (Join-Path $BatchDir "_manifest.json") "$($Dest)_manifest.json" --only-show-errors --region $Region
if ($LASTEXITCODE -ne 0) { throw "manifest upload failed" }
Write-Host "Done: ingestion $($RunPart -replace 'run_date=','')_$($BatchPart -replace 'batch=','')"
