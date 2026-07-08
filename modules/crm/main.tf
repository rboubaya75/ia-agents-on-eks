# =============================================================================
# CRM Orchestrator Module - Main Configuration
# =============================================================================
# Orchestrates the external CRM CDK deployment, creates the workshop Cognito
# user, and writes outputs to SSM Parameter Store.
#
# DESIGN NOTE: All CDK-output-dependent operations (parsing outputs, creating
# Cognito user, writing SSM parameters) happen inside the same local-exec
# provisioner that runs `cdk deploy`. This avoids the plan-time failure that
# occurs with `data.local_file` or `data.aws_ssm_parameter` — those data
# sources are evaluated during `terraform plan`, before any resources exist.
# By keeping everything in a single provisioner, Terraform only needs to know
# that the null_resource should run, not what the CDK outputs will be.
# =============================================================================

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Workshop Password Generation
# -----------------------------------------------------------------------------
# Generated BEFORE CDK deploy so it can be used in the provisioner.

resource "random_password" "crm_workshop_password" {
  length           = 16
  special          = true
  override_special = "!@#$%"
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
}

# -----------------------------------------------------------------------------
# CDK Deployment + Output Parsing + Cognito User + SSM Parameters
# -----------------------------------------------------------------------------
# Single resource that:
# 1. Deploys the CRM CDK app
# 2. Parses CDK outputs JSON
# 3. Writes all values to SSM Parameter Store
# 4. Creates the Cognito workshop user
#
# This avoids the data.local_file plan-time failure on re-runs.

resource "null_resource" "crm_cdk_deploy" {
  triggers = {
    vpc_id       = var.vpc_id
    subnet_ids   = join(",", var.private_subnet_ids)
    region       = var.region
    webhook_url  = var.devops_agent_webhook_url
    username     = var.workshop_username
    crm_app_path = var.crm_app_path
    password     = random_password.crm_workshop_password.result
  }

  provisioner "local-exec" {
    working_dir = var.crm_app_path
    environment = {
      CDK_DEFAULT_ACCOUNT    = data.aws_caller_identity.current.account_id
      CDK_DEFAULT_REGION     = var.region
      WORKSHOP_USERNAME      = var.workshop_username
      WORKSHOP_PASSWORD      = random_password.crm_workshop_password.result
      WORKSHOP_REGION        = var.region
      WORKSHOP_VPC_ID        = var.vpc_id
      WORKSHOP_SUBNET_IDS    = join(",", var.private_subnet_ids)
      WORKSHOP_WEBHOOK_URL   = var.devops_agent_webhook_url
    }
    command     = <<-EOT
      set -e

      # =====================================================================
      # Phase 1: Patch and deploy CDK app
      # =====================================================================

      # Patch bin/app.ts to use CDK_DEFAULT_ACCOUNT/REGION as fallback
      sed -i 's/account: account?.number || account?.id,/account: account?.number || account?.id || process.env.CDK_DEFAULT_ACCOUNT,/' bin/app.ts
      sed -i 's/region: account?.region,/region: account?.region || process.env.CDK_DEFAULT_REGION,/' bin/app.ts

      # Replace placeholder account number in cdk.json
      sed -i "s/\[ACCOUNT_NUMBER\]/$CDK_DEFAULT_ACCOUNT/g" cdk.json

      # Switch Lambda architecture from ARM_64 to X86_64 for CodeBuild (x86)
      sed -i 's/Architecture.ARM_64/Architecture.X86_64/g' lib/common/blueprints.ts

      # Install uv (Python package manager) — required by @aws-cdk/aws-lambda-python-alpha
      # for bundling PythonFunction dependencies. Without it, CDK deploy fails with "uv: not found".
      curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 || pip install uv 2>&1 || echo "WARNING: uv install failed, CDK may fall back to pip"
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

      npm install --silent 2>&1
      npx cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$CDK_DEFAULT_REGION --require-approval never 2>&1 || echo "WARNING: CDK bootstrap had issues (may already be bootstrapped)"
      npx cdk deploy 'dev/**' --require-approval never \
        --context integrationMode=workshop \
        --context workshopVpcId=$WORKSHOP_VPC_ID \
        --context workshopPrivateSubnetIds=$WORKSHOP_SUBNET_IDS \
        --context devOpsAgentWebhookUrl=$WORKSHOP_WEBHOOK_URL \
        --context workshopUsername=$WORKSHOP_USERNAME \
        --outputs-file /tmp/crm-cdk-outputs.json

      # =====================================================================
      # Phase 2: Parse CDK outputs → SSM parameters → Cognito user
      # =====================================================================

      echo "=== Parsing CDK outputs and configuring workshop ==="

      python3 - <<'PYEOF'
import json, subprocess, sys, re, os

region = os.environ['WORKSHOP_REGION']
username = os.environ['WORKSHOP_USERNAME']
password = os.environ['WORKSHOP_PASSWORD']

# --- Parse CDK outputs ---
with open('/tmp/crm-cdk-outputs.json') as f:
    outputs = json.load(f)

backend_key = next((k for k in outputs if 'backend' in k.lower()), None)
frontend_key = next((k for k in outputs if 'frontend' in k.lower() and 'deployment' not in k.lower()), None)

if not backend_key or not frontend_key:
    print(f"ERROR: Could not find backend/frontend stacks. Keys: {list(outputs.keys())}")
    sys.exit(1)

backend = outputs[backend_key]
frontend = outputs[frontend_key]

# Extract Cognito User Pool ID
user_pool_id = next((v for k, v in backend.items()
    if re.search(r'userPool[A-F0-9]', k) and 'userPoolClient' not in k and 'userPoolDomain' not in k), None)

# Extract Cognito Client ID
client_id = next((v for k, v in backend.items() if 'userPoolClient' in k), None)

# Extract CloudFront URL
cloudfront_url = frontend.get('CRMAPPURL', frontend.get('CloudFrontURL', ''))
cloudfront_url = cloudfront_url.replace('https://', '').rstrip('/')

if not user_pool_id:
    print(f"ERROR: Could not find userPoolId. Backend keys: {list(backend.keys())}")
    sys.exit(1)

print(f"  User Pool ID: {user_pool_id}")
print(f"  Client ID:    {client_id}")
print(f"  CloudFront:   {cloudfront_url}")

# --- Write SSM parameters ---
def put_ssm(name, value, param_type='String'):
    subprocess.run([
        'aws', 'ssm', 'put-parameter',
        '--name', name, '--value', value,
        '--type', param_type, '--overwrite',
        '--region', region
    ], check=True)
    print(f"  SSM: {name}")

put_ssm('/workshop/crm/app-url', f'https://{cloudfront_url}')
put_ssm('/workshop/crm/login-password', password, 'SecureString')
put_ssm('/workshop/crm/login-username', username)
put_ssm('/workshop/crm/cognito-user-pool-id', user_pool_id)
put_ssm('/workshop/crm/cognito-client-id', client_id or 'unknown')
put_ssm('/workshop/crm/cloudfront-url', cloudfront_url)

# Extract and store notification queue URL for EKS notification worker
queue_url = next((v for k, v in backend.items() if 'notificationQueue' in k and 'Dlq' not in k and 'QueueUrl' in k), None)
if not queue_url:
    # Fallback: search for any SQS queue URL that isn't the DLQ
    queue_url = next((v for k, v in backend.items() if 'Queue' in k and 'Dlq' not in k and 'sqs.amazonaws.com' in str(v)), None)
if queue_url:
    put_ssm('/workshop/crm/notification-queue-url', queue_url)
    print(f"  Queue URL: {queue_url}")

# --- Create Cognito workshop user ---
print(f"=== Creating Cognito user: {username} ===")

# Create user (may already exist)
result = subprocess.run([
    'aws', 'cognito-idp', 'admin-create-user',
    '--user-pool-id', user_pool_id,
    '--username', username,
    '--temporary-password', password,
    '--message-action', 'SUPPRESS',
    '--user-attributes', f'Name=email,Value={username}', 'Name=email_verified,Value=true',
    '--region', region
], capture_output=True, text=True)
if result.returncode != 0:
    if 'UsernameExistsException' in result.stderr:
        print(f"  User {username} already exists (OK)")
    else:
        print(f"  WARNING: {result.stderr}")

import time; time.sleep(2)

# Set permanent password
subprocess.run([
    'aws', 'cognito-idp', 'admin-set-user-password',
    '--user-pool-id', user_pool_id,
    '--username', username,
    '--password', password,
    '--permanent',
    '--region', region
], check=True)
print(f"  Password set for {username}")

print("=== CRM deployment and configuration complete ===")
PYEOF
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      # Destroy CDK stacks
      cd ${self.triggers.crm_app_path} 2>/dev/null && npx cdk destroy --all --force 2>/dev/null || true

      # Clean up SSM parameters
      for param in \
        /workshop/crm/app-url \
        /workshop/crm/login-password \
        /workshop/crm/login-username \
        /workshop/crm/cognito-user-pool-id \
        /workshop/crm/cognito-client-id \
        /workshop/crm/cloudfront-url; do
        aws ssm delete-parameter --name "$param" --region ${self.triggers.region} 2>/dev/null || true
      done
    EOT
  }
}
