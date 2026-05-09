#!/bin/bash
set -e

export AWS_PROFILE="elevatr"
export AWS_DEFAULT_REGION="us-east-1"

echo "=== Stock Paper Trader - CDK Deploy ==="

cd "$(dirname "$0")/infra"

# Install CDK dependencies
if [ ! -d ".venv" ]; then
    echo "[1/3] Setting up CDK environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt --quiet
else
    source .venv/bin/activate
fi

# Bootstrap (first time only)
echo "[2/3] Bootstrapping CDK (if needed)..."
cdk bootstrap aws://536697230325/us-east-1 2>/dev/null || true

# Deploy
echo "[3/3] Deploying stack..."
cdk deploy --require-approval never

echo ""
echo "=== Done! ==="
echo "Lambdas trigger automatically Mon-Fri:"
echo "  stock-morning-buy:     9:35 AM ET"
echo "  stock-afternoon-close: 4:05 PM ET"
echo "  stock-learn:           4:30 PM ET"
echo ""
echo "Test manually:"
echo "  aws lambda invoke --function-name stock-morning-buy --profile elevatr /dev/stdout"
