<#
.SYNOPSIS
  Health check from your laptop: is the logger running, what has it logged
  recently, and is data arriving in S3?
.EXAMPLE
  .\deploy\status.ps1 -Bucket yourname-transit-raw
#>
param(
    [string]$Bucket = "",
    [string]$Region = "us-east-2"
)

$ErrorActionPreference = "Continue"
$instanceId = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=transit-logger" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" --output text
if (-not $instanceId -or $instanceId -eq "None") { Write-Host "No running transit-logger instance."; exit 1 }
Write-Host "Instance $instanceId is running."

$ping = aws ssm describe-instance-information --region $Region `
    --filters "Key=InstanceIds,Values=$instanceId" --query "InstanceInformationList[0].PingStatus" --output text
if ($ping -ne "Online") {
    Write-Host "Not registered with Session Manager yet (status: $ping). Wait a few minutes after launch and retry."
    exit 1
}

# Commands run on the server. Passed as a JSON file: PowerShell 5.1 mangles
# quotes in native-command arguments, and the CLI's shorthand chokes on "=".
# iconv strips non-ASCII, which crashes the Windows AWS CLI when printed.
$commands = @(
    "systemctl is-active transit-logger transit-upload.timer transit-static.timer",
    "echo '--- API keys present (values hidden):'",
    "grep -oE '^API_[A-Z0-9_]+=.' /etc/transit-logger.env | sed 's/=.$/ yes/'",
    "echo '--- logger:'",
    "journalctl -u transit-logger -n 12 --no-pager -o cat | iconv -c -t ascii",
    "echo '--- static schedules (last run):'",
    "journalctl -u transit-static -n 5 --no-pager -o cat | iconv -c -t ascii",
    "df -h /"
)
$paramFile = Join-Path $env:TEMP "transit-status-params.json"
[IO.File]::WriteAllText($paramFile, (@{ commands = $commands } | ConvertTo-Json))
$cmdId = aws ssm send-command --region $Region --instance-ids $instanceId `
    --document-name AWS-RunShellScript `
    --parameters "file://$paramFile" `
    --query Command.CommandId --output text
Start-Sleep -Seconds 6
Write-Host "`n--- on the server ---"
aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId `
    --query StandardOutputContent --output text

if ($Bucket) {
    $today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
    Write-Host "`n--- S3: today's ($today UTC) snapshot counts ---"
    foreach ($feed in "bart_tripupdates", "act_tripupdates", "act_vehicles", "511_ac_tripupdates", "511_ac_vehiclepositions") {
        # s3 ls handles >1000 objects (list-objects-v2 returns one count per page).
        $n = (aws s3 ls "s3://$Bucket/raw/$feed/$today/" --region $Region | Measure-Object -Line).Lines
        Write-Host ("{0,-26} {1}" -f $feed, $n)
    }
}
