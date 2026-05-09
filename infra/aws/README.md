# AWS Secure Deployment

This folder deploys the secure AWS version of the receipt scanner MVP.

## What It Creates

- Cognito User Pool and web client.
- API Gateway HTTP API protected by a Cognito JWT authorizer.
- Private S3 bucket for receipt uploads.
- KMS key for S3 encryption.
- Lambda API function for upload sessions, receipt reads, edits, and approval.
- Lambda processing function triggered by S3 object-created events.
- Amazon Textract `AnalyzeExpense` integration.
- DynamoDB tables:
  - `stores`
  - `receipts`
  - `receipt_items`
  - `processing_logs`

The DynamoDB design keeps the same logical relationship model:

```text
receipts.id
  -> receipt_items.receipt_id
  -> processing_logs.receipt_id
```

## Before Deploying

Confirm your CLI identity:

```powershell
aws sts get-caller-identity
```

Confirm the region. `us-east-1` works well for Textract `AnalyzeExpense` and is currently the default on this machine.

```powershell
aws configure get region
```

## Deploy

From the repo root:

```powershell
.\infra\aws\deploy.ps1 -StackName receipt-scanner-dev -StageName dev -Region us-east-1 -AllowedCorsOrigin "http://localhost:8000"
```

The script packages the Lambda code, uploads it to an artifact bucket, deploys CloudFormation, and prints outputs.

## After Deploying

Use the CloudFormation outputs:

- `ApiUrl`
- `UserPoolId`
- `UserPoolClientId`
- `ReceiptBucketName`

Create a Cognito test user:

```powershell
aws cognito-idp admin-create-user `
  --user-pool-id <UserPoolId> `
  --username you@example.com `
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

For frontend integration, the browser must obtain a Cognito JWT and call the API with:

```http
Authorization: Bearer <id_token_or_access_token>
```

## Security Notes

- The frontend never receives AWS keys.
- Uploads use short-lived pre-signed S3 URLs.
- S3 public access is blocked.
- S3 objects are encrypted with KMS.
- API Gateway enforces JWT authentication.
- Lambda checks that `receipt.user_id` matches the authenticated Cognito user before returning receipt data.
- The processing Lambda is invoked by S3 events and calls Textract server-side.

## Cost Notes

This creates AWS resources that can incur charges:

- API Gateway requests.
- Lambda invocations.
- S3 storage/requests/KMS usage.
- DynamoDB on-demand reads/writes.
- Textract `AnalyzeExpense` pages.

Delete the stack when testing is finished:

```powershell
aws cloudformation delete-stack --stack-name receipt-scanner-dev --region us-east-1
```
