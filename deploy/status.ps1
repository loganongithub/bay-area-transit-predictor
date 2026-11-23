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

# Commands run on the server. No quotes inside: PowerShell 5.1 mangles them.
$cmdId = aws ssm send-command --region $Region --instance-ids $instanceId `
    --document-name AWS-RunShellScript `
    --parameters "commands=systemctl is-active transit-logger transit-upload.timer,journalctl -u transit-logger -n 15 --no-pager | iconv -c -t ascii,tail -n 5 /var/log/transit-bootstrap.log | iconv -c -t ascii,df -h /" `
    --query Command.CommandId --output text
Start-Sleep -Seconds 6
Write-Host "`n--- on the server ---"
aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId `
    --query StandardOutputContent --output text

if ($Bucket) {
    $today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
    Write-Host "`n--- S3: today's ($today UTC) snapshot counts ---"
    foreach ($feed in "bart_tripupdates", "511_ac_tripupdates", "511_ac_vehiclepositions") {
        $n = aws s3api list-objects-v2 --bucket $Bucket --prefix "raw/$feed/$today/" `
            --query "length(Contents || ``[]``)" --output text
        Write-Host ("{0,-26} {1}" -f $feed, $n)
    }
}
