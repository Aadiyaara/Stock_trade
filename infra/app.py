#!/usr/bin/env python3
import aws_cdk as cdk
from stack import StockTraderStack

app = cdk.App()
StockTraderStack(app, "StockPaperTrader", env=cdk.Environment(
    account="536697230325",
    region="us-east-1",
))
app.synth()
