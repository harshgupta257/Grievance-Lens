# Start the Glue ETL manually for a batch that is already in S3 (recovery path; bypasses the Lambda lock).
#   $env:GL_BUCKET = "<bucket>"; .\aws\scripts\run_etl.ps1 -RunDate 2026-10-27 -BatchId 20261027T101500Z [-Wait]
# Safe to repeat: the same run_date + batch_id always writes and publishes the same curated output.
param([Parameter(Mandatory)][string]$RunDate, [Parameter(Mandatory)][string]$BatchId, [switch]$Wait)
$ErrorActionPreference = "Stop"
if (-not $env:GL_BUCKET) { throw "Set `$env:GL_BUCKET." }
$Region = if ($env:GL_REGION) { $env:GL_REGION } else { "ap-south-1" }
$Job = if ($env:GL_GLUE_JOB) { $env:GL_GLUE_JOB } else { "grievance-lens-etl" }
$b = "s3://$($env:GL_BUCKET)"
$jobArgs = [ordered]@{
  "--run_date" = $RunDate; "--batch_id" = $BatchId
  "--raw_prefix" = "$b/raw/powerbi/run_date=$RunDate/batch=$BatchId/"
  "--curated_root" = "$b/curated/"; "--control_root" = "$b/control/"
  "--contract_uri" = "$b/scripts/contract.json"; "--publish" = "true"
} | ConvertTo-Json -Compress
# Pass the JSON through a file: PowerShell 5.1 mangles embedded quotes in native-command arguments.
$argFile = New-TemporaryFile
Set-Content -Path $argFile -Value $jobArgs -Encoding ascii
$RunId = aws glue start-job-run --job-name $Job --arguments "file://$argFile" --region $Region --query JobRunId --output text
$code = $LASTEXITCODE
Remove-Item $argFile
if ($code -ne 0) { throw "start-job-run failed" }
Write-Host "Started $Job run $RunId for $($RunDate)_$BatchId"
if ($Wait) {
  do {
    Start-Sleep -Seconds 20
    $State = aws glue get-job-run --job-name $Job --run-id $RunId --region $Region --query JobRun.JobRunState --output text
    Write-Host "  $State"
  } while ($State -in @("STARTING", "RUNNING", "STOPPING", "WAITING"))
  if ($State -ne "SUCCEEDED") { exit 1 }
}
