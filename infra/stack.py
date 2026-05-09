from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_lambda as _lambda,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
    aws_iam as iam,
)
from constructs import Construct
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


class StockTraderStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        bucket = s3.Bucket(self, "TradesBucket",
            bucket_name=f"stock-trades-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.GET],
                allowed_origins=["*"],
                allowed_headers=["*"],
            )],
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
        )

        bucket.add_to_resource_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[bucket.arn_for_objects("paper_trades.json")],
            principals=[iam.StarPrincipal()],
        ))

        deps_layer = _lambda.LayerVersion(self, "DepsLayer",
            code=_lambda.Code.from_bucket(
                s3.Bucket.from_bucket_name(self, "DeployBucket", f"stock-trader-deploy-{self.account}"),
                "dependencies-layer.zip",
            ),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="yfinance, pandas, numpy, requests, textblob, bs4",
        )

        lambda_code = _lambda.Code.from_asset(str(PROJECT_ROOT),
            exclude=["infra", "docs", "*.log", "__pycache__", ".git", ".venv", "node_modules"]
        )

        shared_props = {
            "runtime": _lambda.Runtime.PYTHON_3_12,
            "code": lambda_code,
            "layers": [deps_layer],
            "timeout": Duration.minutes(5),
            "memory_size": 1024,
            "environment": {"TRADES_BUCKET": bucket.bucket_name},
        }

        morning_fn = _lambda.Function(self, "MorningBuy",
            function_name="stock-morning-buy",
            handler="lambda/handler.morning_buy",
            **shared_props,
        )

        afternoon_fn = _lambda.Function(self, "AfternoonClose",
            function_name="stock-afternoon-close",
            handler="lambda/handler.afternoon_close",
            **shared_props,
        )

        learn_fn = _lambda.Function(self, "Learn",
            function_name="stock-learn",
            handler="lambda/handler.learn",
            **shared_props,
        )

        bucket.grant_read_write(morning_fn)
        bucket.grant_read_write(afternoon_fn)
        bucket.grant_read_write(learn_fn)

        # 9:35 AM ET = 13:35 UTC
        events.Rule(self, "MorningSchedule",
            rule_name="stock-morning-buy",
            schedule=events.Schedule.cron(minute="35", hour="13", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(morning_fn)],
        )

        # 4:05 PM ET = 20:05 UTC
        events.Rule(self, "AfternoonSchedule",
            rule_name="stock-afternoon-close",
            schedule=events.Schedule.cron(minute="5", hour="20", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(afternoon_fn)],
        )

        # 4:30 PM ET = 20:30 UTC
        events.Rule(self, "LearnSchedule",
            rule_name="stock-learn",
            schedule=events.Schedule.cron(minute="30", hour="20", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(learn_fn)],
        )
