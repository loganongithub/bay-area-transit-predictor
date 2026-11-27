<#
.SYNOPSIS
  Download archived snapshots for one day from S3 into data/raw/ on your laptop.
  Uses "aws s3 sync", so re-running only fetches files you don't have yet.

.EXAMPLE
  .\tools\sync.ps1 -Day 2026-11-27                       # AC Transit API feeds (default)
  .\tools\sync.ps1 -Day 2026-11-27 -Feeds bart_tripupdates
  .\tools\sync.ps1 -Day 2026-11-27 -Feeds static          # static GTFS versions (all days)

  Days are UTC dates (the folder names in S3). Run with:
  powershell -ExecutionPolicy Bypass -File .\tools\sync.ps1 -Day 2026-11-27
#>
param(
    [Parameter(Mandatory = $true)][string]$Day,
    [string[]]$Feeds = @("act_tripupdates", "act_vehicles"),
    [string]$Bucket = "logan-transit-raw-2026",
    [string]$Region = "us-east-2"
)

$ErrorActionPreference = "Continue"
# With "powershell -File", a list like a,b arrives as one string: split it.
$Feeds = @($Feeds | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

foreach ($feed in $Feeds) {
    if ($feed -eq "static") {
        $src = "s3://$Bucket/raw/static"
        $dst = Join-Path $root "data\raw\static"
    } else {
        $src = "s3://$Bucket/raw/$feed/$Day"
        $dst = Join-Path $root "data\raw\$feed\$Day"
    }
    Write-Host "== $src -> $dst"
    aws s3 sync $src $dst --region $Region --only-show-errors
    if ($LASTEXITCODE -ne 0) { Write-Host "sync failed for $feed (expired login? run: aws login)"; continue }
    $n = (Get-ChildItem $dst -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "   $n files locally"
}
