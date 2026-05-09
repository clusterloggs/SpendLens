param(
  [string]$StackName = "receipt-scanner-dev",
  [string]$ProjectName = "receipt-scanner",
  [string]$StageName = "dev",
  [string]$Region = "",
  [string]$AllowedCorsOrigin = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

if (-not $Region) {
  $Region = aws configure get region
  if (-not $Region) {
    $Region = "us-east-1"
  }
}

$AccountId = aws sts get-caller-identity --query Account --output text
$ArtifactBucket = "$ProjectName-$StageName-artifacts-$AccountId-$Region"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$BuildDir = Join-Path $Root ".aws-build"
$ApiZip = Join-Path $BuildDir "api.zip"
$ProcessZip = Join-Path $BuildDir "process.zip"

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $ApiZip, $ProcessZip

Compress-Archive -Path (Join-Path $PSScriptRoot "lambda\api_handler.py") -DestinationPath $ApiZip
Compress-Archive -Path (Join-Path $PSScriptRoot "lambda\process_handler.py") -DestinationPath $ProcessZip

$bucketExists = aws s3api head-bucket --bucket $ArtifactBucket 2>$null
if ($LASTEXITCODE -ne 0) {
  if ($Region -eq "us-east-1") {
    aws s3api create-bucket --bucket $ArtifactBucket --region $Region | Out-Null
  } else {
    aws s3api create-bucket --bucket $ArtifactBucket --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null
  }
  aws s3api put-public-access-block --bucket $ArtifactBucket --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true | Out-Null
}

$ApiKey = "lambda/api-$((Get-FileHash $ApiZip -Algorithm SHA256).Hash).zip"
$ProcessKey = "lambda/process-$((Get-FileHash $ProcessZip -Algorithm SHA256).Hash).zip"

aws s3 cp $ApiZip "s3://$ArtifactBucket/$ApiKey" --region $Region | Out-Null
aws s3 cp $ProcessZip "s3://$ArtifactBucket/$ProcessKey" --region $Region | Out-Null

aws cloudformation deploy `
  --stack-name $StackName `
  --template-file (Join-Path $PSScriptRoot "template.yaml") `
  --region $Region `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=$ProjectName `
    StageName=$StageName `
    AllowedCorsOrigin=$AllowedCorsOrigin `
    ApiLambdaCodeBucket=$ArtifactBucket `
    ApiLambdaCodeKey=$ApiKey `
    ProcessLambdaCodeBucket=$ArtifactBucket `
    ProcessLambdaCodeKey=$ProcessKey

aws cloudformation describe-stacks `
  --stack-name $StackName `
  --region $Region `
  --query "Stacks[0].Outputs" `
  --output table
