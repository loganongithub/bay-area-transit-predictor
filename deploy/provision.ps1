<#
.SYNOPSIS
  Build the Phase 0 logger server from your laptop: S3 bucket, IAM role,
  firewall, EC2 instance, and optionally a monthly budget alert (default $15).

.EXAMPLE
  .\deploy\provision.ps1 -Bucket yourname-transit-raw -AlertEmail you@example.com

  Safe to re-run: anything that already exists is reused, not duplicated.
  Needs the AWS CLI, logged in as an admin (aws sts get-caller-identity works).
#>
param(
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Region = "us-east-2",
    [string]$InstanceType = "t4g.micro",
    [string]$HealthcheckUrl = "",
    [string]$AlertEmail = "",
    [int]$BudgetUsd = 15
)

# Native-command errors are checked through exit codes, not PowerShell errors.
$ErrorActionPreference = "Continue"
$Name = "transit-logger"
$Role = "transit-logger-role"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path $env:TEMP "transit-provision"
New-Item -ItemType Directory -Force $tmp | Out-Null

function Invoke-Aws {
    $out = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "FAILED: aws $($args -join ' ')" }
    return $out
}
function Test-Aws {
    & aws @args 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Write-JsonFile([string]$fileName, [string]$json) {
    $path = Join-Path $tmp $fileName
    [IO.File]::WriteAllText($path, $json)   # UTF-8 without BOM: the CLI rejects BOMs
    return "file://$path"
}
function Step([string]$msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

Step "Checking AWS login"
$account = Invoke-Aws sts get-caller-identity --query Account --output text
Write-Host "account $account, region $Region"

# ---------------------------------------------------------------- S3
Step "S3 bucket $Bucket"
if (Test-Aws s3api head-bucket --bucket $Bucket) {
    Write-Host "exists"
} else {
    Invoke-Aws s3api create-bucket --bucket $Bucket --region $Region `
        --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    Write-Host "created"
}
Invoke-Aws s3api put-public-access-block --bucket $Bucket --public-access-block-configuration `
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" | Out-Null

# ---------------------------------------------------------------- IAM
Step "IAM role $Role"
$trust = Write-JsonFile "trust.json" @'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
'@
if (Test-Aws iam get-role --role-name $Role) {
    Write-Host "exists"
} else {
    Invoke-Aws iam create-role --role-name $Role --assume-role-policy-document $trust | Out-Null
    Write-Host "created"
}
# Session Manager login (no SSH)
Invoke-Aws iam attach-role-policy --role-name $Role `
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore | Out-Null
# Write snapshots to this one bucket, nothing else
$s3Policy = (Get-Content (Join-Path $here "iam-s3-policy.json") -Raw).Replace("YOUR-BUCKET-NAME", $Bucket)
Invoke-Aws iam put-role-policy --role-name $Role --policy-name write-raw-snapshots `
    --policy-document (Write-JsonFile "s3-policy.json" $s3Policy) | Out-Null
# Read the 511 key from Parameter Store
$ssmPolicy = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ssm:GetParameter","Resource":"arn:aws:ssm:' + $Region + ':' + $account + ':parameter/transit-logger/*"}]}'
Invoke-Aws iam put-role-policy --role-name $Role --policy-name read-511-key `
    --policy-document (Write-JsonFile "ssm-policy.json" $ssmPolicy) | Out-Null

if (-not (Test-Aws iam get-instance-profile --instance-profile-name $Role)) {
    Invoke-Aws iam create-instance-profile --instance-profile-name $Role | Out-Null
    Invoke-Aws iam add-role-to-instance-profile --instance-profile-name $Role --role-name $Role | Out-Null
    Write-Host "instance profile created; waiting for IAM to propagate"
    Start-Sleep -Seconds 15
}

# ---------------------------------------------------------------- network
Step "Security group (no inbound rules)"
$vpc = Invoke-Aws ec2 describe-vpcs --region $Region --filters "Name=isDefault,Values=true" `
    --query "Vpcs[0].VpcId" --output text
if ($vpc -eq "None") { throw "No default VPC in $Region. Create one: aws ec2 create-default-vpc --region $Region" }
$sg = Invoke-Aws ec2 describe-security-groups --region $Region `
    --filters "Name=group-name,Values=$Name" "Name=vpc-id,Values=$vpc" `
    --query "SecurityGroups[0].GroupId" --output text
if ($sg -eq "None") {
    $sg = Invoke-Aws ec2 create-security-group --region $Region --group-name $Name `
        --description "transit logger - no inbound access" --vpc-id $vpc --query GroupId --output text
    Write-Host "created $sg"
} else { Write-Host "exists $sg" }

# ---------------------------------------------------------------- EC2
Step "EC2 instance"
$existing = Invoke-Aws ec2 describe-instances --region $Region `
    --filters "Name=tag:Name,Values=$Name" "Name=instance-state-name,Values=pending,running,stopping,stopped" `
    --query "Reservations[].Instances[].InstanceId" --output text
if ($existing) {
    Write-Host "already exists: $existing (not launching another)"
    $instanceId = $existing
} else {
    $arch = if ($InstanceType -match '^[a-z]+\d+g') { "arm64" } else { "x86_64" }
    $ami = Invoke-Aws ssm get-parameter --region $Region `
        --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-$arch" `
        --query Parameter.Value --output text
    Write-Host "Amazon Linux 2023 $arch AMI: $ami"

    $userData = (Get-Content (Join-Path $here "user-data.sh") -Raw).Replace("`r`n", "`n")
    $userData = $userData.Replace("__BUCKET__", $Bucket).Replace("__REGION__", $Region).Replace("__HEALTHCHECK__", $HealthcheckUrl)
    $udPath = Join-Path $tmp "user-data.sh"
    [IO.File]::WriteAllText($udPath, $userData)

    $instanceId = $null
    foreach ($attempt in 1..5) {   # a brand-new instance profile can take a minute to be usable
        $instanceId = & aws ec2 run-instances --region $Region --image-id $ami --instance-type $InstanceType `
            --iam-instance-profile "Name=$Role" --security-group-ids $sg `
            --metadata-options "HttpTokens=required,HttpEndpoint=enabled" `
            --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=16,VolumeType=gp3}" `
            --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$Name}]" `
            --user-data "file://$udPath" --query "Instances[0].InstanceId" --output text
        if ($LASTEXITCODE -eq 0) { break }
        Write-Host "launch failed (attempt $attempt), retrying in 15s..."
        $instanceId = $null
        Start-Sleep -Seconds 15
    }
    if (-not $instanceId) { throw "Could not launch the instance; see the errors above." }
    Write-Host "launched $instanceId, waiting for it to run..."
    Invoke-Aws ec2 wait instance-running --region $Region --instance-ids $instanceId | Out-Null
}

# ---------------------------------------------------------------- budget
if ($AlertEmail) {
    Step "Budget alert: email $AlertEmail when monthly spend passes `$$BudgetUsd"
    if (Test-Aws budgets describe-budget --account-id $account --budget-name transit-monthly) {
        Write-Host "exists"
    } else {
        $budget = Write-JsonFile "budget.json" ('{"BudgetName":"transit-monthly","BudgetLimit":{"Amount":"' + $BudgetUsd + '","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}')
        $notif = Write-JsonFile "notif.json" ('[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"' + $AlertEmail + '"}]}]')
        Invoke-Aws budgets create-budget --account-id $account --budget $budget `
            --notifications-with-subscribers $notif | Out-Null
        Write-Host "created"
    }
}

Step "Done"
Write-Host "Instance: $instanceId"
Write-Host "It installs itself over the next ~3 minutes. Then run:"
Write-Host "  .\deploy\status.ps1"
Write-Host "Once your API keys are in .env:"
Write-Host "  .\deploy\set-511-key.ps1"
