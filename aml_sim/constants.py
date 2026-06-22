"""Shared constants for AML-Sim."""

from __future__ import annotations

# Exchange modes
EXCHANGE_MODE_ORDERBOOK = "orderbook"
EXCHANGE_MODE_CANDLE = "candle"

# Data sources
DATA_SOURCE_POLYGON = "polygon"
DATA_SOURCE_ALPHA_VANTAGE = "alpha_vantage"
DATA_SOURCE_SYNTHETIC = "synthetic"

# AML agent type identifiers (used in YAML scenario files)
AGENT_TYPE_MARKET_MAKER = "AML_Market_Maker"
AGENT_TYPE_RETAIL_TRADER = "AML_Retail_Trader"
AGENT_TYPE_INSTITUTIONAL_TRADER = "AML_Institutional_Trader"

# StockSim agent types (not AML-owned, but referenced in orchestration)
AGENT_TYPE_LLM_TRADER = "LLMTradingAgent"
AGENT_TYPE_RANDOM_TRADER = "Random_Trader"
