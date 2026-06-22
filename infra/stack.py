import os
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
            resources=[
                bucket.arn_for_objects("paper_trades.json"),
                bucket.arn_for_objects("recommendations.json"),
            ],
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
            "environment": {
                "TRADES_BUCKET": bucket.bucket_name,
                "POLYGON_API_KEY": "DT3pw8H1EFAcMF8LtysDQwOMfmtyAzqO",
            },
        }

        recommend_fn = _lambda.Function(self, "PreMarketRecommend",
            function_name="stock-recommend",
            handler="lambda/handler.pre_market_recommend",
            **shared_props,
        )

        alpaca_env = {
            "FINNHUB_API_KEY": "d28r241r01qle9gs976gd28r241r01qle9gs9770",
            "ALPACA_API_KEY": os.environ.get("ALPACA_API_KEY", ""),
            "ALPACA_SECRET_KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
            "ALPACA_LIVE_TRADING": os.environ.get("ALPACA_LIVE_TRADING", "false"),
            "LIVE_DAILY_BUDGET": os.environ.get("LIVE_DAILY_BUDGET", "1000"),
        }

        morning_fn = _lambda.Function(self, "MorningBuy",
            function_name="stock-morning-buy",
            handler="lambda/handler.morning_buy",
            **{**shared_props, "environment": {
                **shared_props["environment"],
                **alpaca_env,
            }},
        )

        close_fn = _lambda.Function(self, "CloseAndLearn",
            function_name="stock-close-and-learn",
            handler="lambda/handler.close_and_learn",
            **{**shared_props, "environment": {
                **shared_props["environment"],
                **alpaca_env,
            }},
        )

        midday_fn = _lambda.Function(self, "MiddayExitCheck",
            function_name="stock-midday-exit",
            handler="lambda/handler.midday_exit_check",
            **{**shared_props, "environment": {
                **shared_props["environment"],
                **alpaca_env,
            }},
        )

        cache_props = {**shared_props, "timeout": Duration.minutes(10)}
        cache_fn = _lambda.Function(self, "BuildCache",
            function_name="stock-build-cache",
            handler="lambda/handler.build_cache",
            **cache_props,
        )

        bucket.grant_read_write(recommend_fn)
        bucket.grant_read_write(morning_fn)
        bucket.grant_read_write(close_fn)
        bucket.grant_read_write(midday_fn)
        bucket.grant_read_write(cache_fn)

        # Cache runs every 30min from 12AM-4AM ET (4-8 UTC) — 50 tickers/run × 10 runs = 500 tickers
        events.Rule(self, "CacheSchedule",
            rule_name="stock-build-cache",
            schedule=events.Schedule.cron(minute="0,30", hour="4,5,6,7,8", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(cache_fn)],
        )

        # 4:30 AM ET = 8:30 UTC
        events.Rule(self, "PreMarketSchedule",
            rule_name="stock-recommend",
            schedule=events.Schedule.cron(minute="30", hour="8", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(recommend_fn)],
        )

        # 9:35 AM ET = 13:35 UTC
        events.Rule(self, "MorningSchedule",
            rule_name="stock-morning-buy",
            schedule=events.Schedule.cron(minute="35", hour="13", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(morning_fn)],
        )

        # 11:00 AM ET = 15:00 UTC, 1:00 PM ET = 17:00 UTC
        events.Rule(self, "MiddaySchedule",
            rule_name="stock-midday-exit",
            schedule=events.Schedule.cron(minute="0", hour="15,17", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(midday_fn)],
        )

        # 4:05 PM ET = 20:05 UTC
        events.Rule(self, "CloseSchedule",
            rule_name="stock-close-and-learn",
            schedule=events.Schedule.cron(minute="5", hour="20", week_day="MON-FRI"),
            targets=[targets.LambdaFunction(close_fn)],
        )
