#!/bin/bash

# EKS Retail Store Deployment Script for Unified Workshop
# Deploys with AWS backend services (Aurora MySQL/PostgreSQL, DynamoDB, ElastiCache, SQS)
# NOTE: Do NOT use set -e - we want deployments to continue even if some timeout

echo "=========================================="
echo "  EKS Retail Store Deployment"
echo "=========================================="

# Configuration
CHART_VERSION="${CHART_VERSION:-0.8.5}"
IMAGE_TAG="${IMAGE_TAG:-1.3.0}"
TERRAFORM_DIR="${TERRAFORM_DIR:-/home/ec2-user/environment/terraform}"
CLUSTER_NAME="${CLUSTER_NAME:-devops-agent-eks-cluster}"
AWS_REGION="${AWS_REGION:-$(curl -s http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo 'us-east-1')}"

# ECR Chart URLs
ECR_REGISTRY="public.ecr.aws/aws-containers"
CATALOG_CHART="oci://${ECR_REGISTRY}/retail-store-sample-catalog-chart"
CART_CHART="oci://${ECR_REGISTRY}/retail-store-sample-cart-chart"
CHECKOUT_CHART="oci://${ECR_REGISTRY}/retail-store-sample-checkout-chart"
ORDERS_CHART="oci://${ECR_REGISTRY}/retail-store-sample-orders-chart"
UI_CHART="oci://${ECR_REGISTRY}/retail-store-sample-ui-chart"

# Check prerequisites
for cmd in kubectl helm terraform aws; do
    if ! command -v $cmd &> /dev/null; then
        echo "[ERROR] $cmd is not installed"
        exit 1
    fi
done

# Auto-configure kubeconfig
echo "[INFO] Configuring kubeconfig for cluster: $CLUSTER_NAME"
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" 2>/dev/null || true

# Verify cluster connection
echo "[INFO] Verifying cluster connection..."
if ! kubectl cluster-info &> /dev/null; then
    echo "[ERROR] Cannot connect to Kubernetes cluster"
    exit 1
fi

# Get Terraform outputs (EKS-specific outputs from unified workshop)
echo "[INFO] Reading Terraform outputs..."
get_tf_output() {
    terraform -chdir="$TERRAFORM_DIR" output -raw "$1" 2>/dev/null || echo ""
}

# Use EKS-specific outputs from unified workshop
CATALOG_DB_ENDPOINT=$(get_tf_output "eks_catalog_db_endpoint")
ORDERS_DB_ENDPOINT=$(get_tf_output "eks_orders_db_endpoint")
DYNAMODB_TABLE=$(get_tf_output "eks_carts_dynamodb_table_name")
ELASTICACHE_ENDPOINT=$(get_tf_output "eks_checkout_redis_endpoint")
MQ_ENDPOINT=""  # No longer used - replaced by SQS
CARTS_IAM_ROLE=$(get_tf_output "eks_carts_iam_role_arn")
ORDERS_IAM_ROLE=$(get_tf_output "eks_orders_iam_role_arn")
ORDERS_SQS_QUEUE=$(get_tf_output "eks_orders_sqs_queue_name")

# Get passwords from terraform outputs (sensitive outputs)
CATALOG_DB_PASSWORD=$(terraform -chdir="$TERRAFORM_DIR" output -raw eks_catalog_db_password 2>/dev/null || echo "")
ORDERS_DB_PASSWORD=$(terraform -chdir="$TERRAFORM_DIR" output -raw eks_orders_db_password 2>/dev/null || echo "")

# Check if we have AWS backends
if [ -z "$CATALOG_DB_ENDPOINT" ] || [ -z "$DYNAMODB_TABLE" ]; then
    echo "[ERROR] Terraform outputs not found. Cannot deploy without AWS backends."
    echo "[INFO] Make sure EKS is enabled (enable_eks=true) and terraform apply completed."
    exit 1
fi

echo "[INFO] Found AWS backend services:"
echo "  - Catalog DB: $CATALOG_DB_ENDPOINT"
echo "  - Orders DB: $ORDERS_DB_ENDPOINT"
echo "  - DynamoDB: $DYNAMODB_TABLE"
echo "  - ElastiCache: $ELASTICACHE_ENDPOINT"
echo "  - Orders SQS: $ORDERS_SQS_QUEUE"
echo "  - Carts IAM Role: $CARTS_IAM_ROLE"
echo "  - Orders IAM Role: $ORDERS_IAM_ROLE"

# Create namespaces with Application Signals instrumentation enabled
echo "[INFO] Creating namespaces with Application Signals instrumentation..."
for ns in catalog carts checkout orders ui; do
    kubectl create namespace $ns --dry-run=client -o yaml | kubectl apply -f -
    # Enable CloudWatch Application Signals auto-instrumentation for Java apps
    kubectl label namespace $ns aws-observability=enabled --overwrite 2>/dev/null || true
done

# Create Instrumentation CR for Application Signals (Java auto-instrumentation)
echo "[INFO] Creating Application Signals Instrumentation resources..."
cat <<EOF | kubectl apply -f -
apiVersion: opentelemetry.io/v1alpha1
kind: Instrumentation
metadata:
  name: java-instrumentation
  namespace: catalog
spec:
  propagators:
    - tracecontext
    - baggage
    - xray
  sampler:
    type: always_on
  java:
    image: public.ecr.aws/aws-observability/adot-autoinstrumentation-java:v1.32.6
    env:
      - name: OTEL_AWS_APPLICATION_SIGNALS_ENABLED
        value: "true"
      - name: OTEL_AWS_APPLICATION_SIGNALS_EXPORTER_ENDPOINT
        value: "http://cloudwatch-agent.amazon-cloudwatch:4316/v1/metrics"
      - name: OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
        value: "http://cloudwatch-agent.amazon-cloudwatch:4316/v1/traces"
      - name: OTEL_METRICS_EXPORTER
        value: "none"
      - name: OTEL_LOGS_EXPORTER
        value: "none"
EOF

# Apply same instrumentation to other Java service namespaces
for ns in carts checkout orders; do
    cat <<EOF | kubectl apply -f -
apiVersion: opentelemetry.io/v1alpha1
kind: Instrumentation
metadata:
  name: java-instrumentation
  namespace: $ns
spec:
  propagators:
    - tracecontext
    - baggage
    - xray
  sampler:
    type: always_on
  java:
    image: public.ecr.aws/aws-observability/adot-autoinstrumentation-java:v1.32.6
    env:
      - name: OTEL_AWS_APPLICATION_SIGNALS_ENABLED
        value: "true"
      - name: OTEL_AWS_APPLICATION_SIGNALS_EXPORTER_ENDPOINT
        value: "http://cloudwatch-agent.amazon-cloudwatch:4316/v1/metrics"
      - name: OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
        value: "http://cloudwatch-agent.amazon-cloudwatch:4316/v1/traces"
      - name: OTEL_METRICS_EXPORTER
        value: "none"
      - name: OTEL_LOGS_EXPORTER
        value: "none"
EOF
done

# ==========================================================================
# CATALOG - Aurora MySQL
# ==========================================================================
echo "[INFO] Deploying Catalog service (Aurora MySQL)..."

kubectl create configmap catalog-config \
    --namespace catalog \
    --from-literal=RETAIL_CATALOG_PERSISTENCE_PROVIDER=mysql \
    --from-literal=RETAIL_CATALOG_PERSISTENCE_ENDPOINT="${CATALOG_DB_ENDPOINT}:3306" \
    --from-literal=RETAIL_CATALOG_PERSISTENCE_DB_NAME=catalog \
    --from-literal=RETAIL_CATALOG_PERSISTENCE_USER=root \
    --from-literal=RETAIL_CATALOG_PERSISTENCE_PASSWORD="${CATALOG_DB_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic catalog-db \
    --namespace catalog \
    --from-literal=endpoint="${CATALOG_DB_ENDPOINT}" \
    --from-literal=username="root" \
    --from-literal=password="${CATALOG_DB_PASSWORD}" \
    --from-literal=name="catalog" \
    --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install catalog $CATALOG_CHART \
    --version $CHART_VERSION \
    --namespace catalog \
    --set image.tag=$IMAGE_TAG \
    --set mysql.create=false \
    --set mysql.endpoint="${CATALOG_DB_ENDPOINT}" \
    --set mysql.database=catalog \
    --set mysql.secret.create=false \
    --set mysql.secret.name=catalog-db \
    --timeout 3m || echo "[WARNING] Catalog deployment issue, continuing..."

kubectl patch deployment catalog -n catalog --type='strategic' \
    -p='{"spec":{"template":{"spec":{"containers":[{"name":"catalog","envFrom":[{"configMapRef":{"name":"catalog-config"}}]}]}}}}' 2>/dev/null || true

# ==========================================================================
# CARTS - DynamoDB
# ==========================================================================
echo "[INFO] Deploying Carts service (DynamoDB)..."
helm upgrade --install carts $CART_CHART \
    --version $CHART_VERSION \
    --namespace carts \
    --set image.tag=$IMAGE_TAG \
    --set dynamodb.create=false \
    --set dynamodb.tableName="${DYNAMODB_TABLE}" \
    --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${CARTS_IAM_ROLE}" \
    --timeout 3m || echo "[WARNING] Carts deployment issue, continuing..."

# ==========================================================================
# ORDERS - Aurora PostgreSQL + SQS
# ==========================================================================
echo "[INFO] Deploying Orders service (Aurora PostgreSQL + SQS)..."

kubectl create secret generic orders-db \
    --namespace orders \
    --from-literal=username=root \
    --from-literal=password="${ORDERS_DB_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install orders $ORDERS_CHART \
    --version $CHART_VERSION \
    --namespace orders \
    --set image.tag=$IMAGE_TAG \
    --set postgresql.create=false \
    --set postgresql.endpoint.host="${ORDERS_DB_ENDPOINT}" \
    --set postgresql.endpoint.port=5432 \
    --set postgresql.database=orders \
    --set postgresql.secret.create=false \
    --set postgresql.secret.name=orders-db \
    --set rabbitmq.create=false \
    --set env[0].name=RETAIL_ORDERS_MESSAGING_PROVIDER \
    --set env[0].value=sqs \
    --set env[1].name=RETAIL_ORDERS_MESSAGING_SQS_TOPIC \
    --set env[1].value="${ORDERS_SQS_QUEUE}" \
    --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${ORDERS_IAM_ROLE}" \
    --timeout 3m || echo "[WARNING] Orders deployment issue, continuing..."

# ==========================================================================
# CHECKOUT - ElastiCache Redis
# ==========================================================================
echo "[INFO] Deploying Checkout service (ElastiCache Redis)..."
helm upgrade --install checkout $CHECKOUT_CHART \
    --version $CHART_VERSION \
    --namespace checkout \
    --set image.tag=$IMAGE_TAG \
    --set redis.create=false \
    --set redis.endpoint="${ELASTICACHE_ENDPOINT}" \
    --set app.endpoints.orders=http://orders.orders.svc:80 \
    --timeout 3m || echo "[WARNING] Checkout deployment issue, continuing..."

# ==========================================================================
# UI - Frontend with ALB Ingress
# ==========================================================================
echo "[INFO] Deploying UI service..."
helm upgrade --install ui $UI_CHART \
    --version $CHART_VERSION \
    --namespace ui \
    --set image.tag=$IMAGE_TAG \
    --set app.endpoints.catalog=http://catalog.catalog.svc \
    --set app.endpoints.carts=http://carts.carts.svc \
    --set app.endpoints.checkout=http://checkout.checkout.svc \
    --set app.endpoints.orders=http://orders.orders.svc \
    --set ingress.enabled=true \
    --set ingress.className=alb \
    --set 'ingress.annotations.alb\.ingress\.kubernetes\.io/scheme=internet-facing' \
    --set 'ingress.annotations.alb\.ingress\.kubernetes\.io/target-type=ip' \
    --timeout 3m || echo "[WARNING] UI deployment issue, continuing..."

echo "[INFO] Helm releases created. Pods are starting in background..."
echo "[INFO] Check status with: kubectl get pods -A"
echo ""
echo "=========================================="
echo "  EKS Deployment Initiated Successfully"
echo "=========================================="
echo ""
echo "To get the application URL, run:"
echo "  kubectl get ingress -n ui"
echo ""
echo "Or use the get-app-urls.sh script"

exit 0
