#!/bin/bash
set -e

ACCOUNT_ID="536697230325"
REGION="us-east-1"
REPO_NAME="stock-trader"
IMAGE_TAG="latest"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"
STACK_NAME="stock-paper-trader"

echo "=== Stock Paper Trader - Deploy ==="

# Step 1: Create ECR repo (if not exists)
echo "[1/5] Creating ECR repository..."
aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION 2>/dev/null || \
  aws ecr create-repository --repository-name $REPO_NAME --region $REGION

# Step 2: Build Docker image
echo "[2/5] Building Docker image..."
docker build -t $REPO_NAME .

# Step 3: Push to ECR
echo "[3/5] Pushing to ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
docker tag $REPO_NAME:latest $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:$IMAGE_TAG

# Step 4: Deploy CloudFormation
echo "[4/5] Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file infra/template-docker.yaml \
  --stack-name $STACK_NAME \
  --parameter-overrides ImageUri="${ECR_URI}:${IMAGE_TAG}" \
  --capabilities CAPABILITY_IAM \
  --region $REGION

# Step 5: Verify
echo "[5/5] Verifying deployment..."
aws lambda list-functions --region $REGION --query "Functions[?starts_with(FunctionName, 'stock-')].{Name:FunctionName, Runtime:Runtime}" --output table

echo ""
echo "=== Deployment complete! ==="
echo "Functions will trigger automatically:"
echo "  - stock-morning-buy:     9:35 AM ET (Mon-Fri)"
echo "  - stock-afternoon-close: 4:05 PM ET (Mon-Fri)"
echo "  - stock-learn:           4:30 PM ET (Mon-Fri)"
echo ""
echo "To test manually:"
echo "  aws lambda invoke --function-name stock-morning-buy /dev/stdout"
