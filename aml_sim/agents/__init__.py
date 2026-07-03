"""AML-specific trading agents owned by AML-Sim."""

__all__ = [
    "AgentProfile",
    "AMLInformedTrader",
    "AMLInstitutionalTrader",
    "AMLLiquidityTaker",
    "AMLMarketMakerTrader",
    "AMLRetailTrader",
    "AMLShockAgent",
    "InformedProfile",
    "InstitutionalProfile",
    "LiquidityTakerProfile",
    "MarketMakerProfile",
    "RetailProfile",
]


def __getattr__(name: str):
    # Lazy imports are required because agent modules import from StockSim
    # (e.g. utils.orders, agents.benchmark_traders.trader) which is only
    # available at launch time after ensure_stocksim_import_path runs.
    if name in {
        "AgentProfile",
        "InformedProfile",
        "InstitutionalProfile",
        "LiquidityTakerProfile",
        "MarketMakerProfile",
        "RetailProfile",
    }:
        from aml_sim.agents.models.profile import (
            AgentProfile,
            InformedProfile,
            InstitutionalProfile,
            LiquidityTakerProfile,
            MarketMakerProfile,
            RetailProfile,
        )

        return {
            "AgentProfile": AgentProfile,
            "InformedProfile": InformedProfile,
            "InstitutionalProfile": InstitutionalProfile,
            "LiquidityTakerProfile": LiquidityTakerProfile,
            "MarketMakerProfile": MarketMakerProfile,
            "RetailProfile": RetailProfile,
        }[name]
    if name == "AMLInformedTrader":
        from aml_sim.agents.informed_trader import AMLInformedTrader

        return AMLInformedTrader
    if name == "AMLInstitutionalTrader":
        from aml_sim.agents.institutional_trader import AMLInstitutionalTrader

        return AMLInstitutionalTrader
    if name == "AMLLiquidityTaker":
        from aml_sim.agents.liquidity_taker import AMLLiquidityTaker

        return AMLLiquidityTaker
    if name == "AMLMarketMakerTrader":
        from aml_sim.agents.market_maker_trader import AMLMarketMakerTrader

        return AMLMarketMakerTrader
    if name == "AMLRetailTrader":
        from aml_sim.agents.retail_trader import AMLRetailTrader

        return AMLRetailTrader
    if name == "AMLShockAgent":
        from aml_sim.agents.shock_agent import AMLShockAgent

        return AMLShockAgent

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
