<#
.SYNOPSIS
  Store the API keys from your local .env in SSM Parameter Store (encrypted),
  then tell the server to load them and restart the logger.
  Keys never go into git, user data, or a shell you type into.

  .env           Parameter Store                 used for
  API_511_KEY    /transit-logger/api-511-key     511.org feeds
  API_ACT_TOKEN  /transit-logger/act-token       AC Transit's own API
#>
param([string]$Region = "us-east-2")

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw "No .env at $envFile. Copy .env.example to .env and fill in the keys." }

$map = @{ "API_511_KEY" = "/transit-logger/api-511-key"; "API_ACT_TOKEN" = "/transit-logger/act-token" }
$stored = 0
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*(API_511_KEY|API_ACT_TOKEN)\s*=\s*(\S+)\s*$') {
        $var = $Matches[1]; $value = $Matches[2].Trim('"', "'")
        aws ssm put-parameter --region $Region --name $map[$var] --type SecureString --value $value --overwrite | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not store $var in Parameter Store" }
        Write-Host "$var stored in Parameter Store ($($map[$var]))."
        $stored++
    }
}
if ($stored -eq 0) { throw "No API_511_KEY or API_ACT_TOKEN values found in .env" }

$instanceId = aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=transit-logger" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" --output text
if (-not $instanceId -or $instanceId -eq "None") { throw "No running transit-logger instance found" }

$cmdId = aws ssm send-command --region $Region --instance-ids $instanceId `
    --document-name AWS-RunShellScript `
    --parameters "commands=/opt/transit-logger/load-keys.sh" `
    --query Command.CommandId --output text
if ($LASTEXITCODE -ne 0) { throw "send-command failed (is the instance registered with Session Manager yet?)" }

Start-Sleep -Seconds 8
aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId `
    --query "[Status, StandardOutputContent, StandardErrorContent]" --output text
