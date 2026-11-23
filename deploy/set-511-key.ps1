<#
.SYNOPSIS
  Store the 511 key from your local .env in SSM Parameter Store (encrypted),
  then tell the server to load it and restart the logger.
  The key never goes into git, user data, or a shell you type into.
#>
param([string]$Region = "us-west-2")

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw "No .env at $envFile. Copy .env.example to .env and set API_511_KEY." }

$key = $null
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*API_511_KEY\s*=\s*(\S+)\s*$') { $key = $Matches[1].Trim('"', "'") }
}
if (-not $key) { throw "API_511_KEY is empty in .env" }

aws ssm put-parameter --region $Region --name /transit-logger/api-511-key `
    --type SecureString --value $key --overwrite | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not store the key in Parameter Store" }
Write-Host "Key stored in Parameter Store (/transit-logger/api-511-key)."

$instanceId = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=transit-logger" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" --output text
if (-not $instanceId -or $instanceId -eq "None") { throw "No running transit-logger instance found" }

$cmdId = aws ssm send-command --region $Region --instance-ids $instanceId `
    --document-name AWS-RunShellScript `
    --parameters "commands=/opt/transit-logger/load-511-key.sh" `
    --query Command.CommandId --output text
if ($LASTEXITCODE -ne 0) { throw "send-command failed (is the instance registered with Session Manager yet?)" }

Start-Sleep -Seconds 8
aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId `
    --query "[Status, StandardOutputContent, StandardErrorContent]" --output text
