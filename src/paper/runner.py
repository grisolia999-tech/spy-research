"""Single-tick paper runner. Orchestrates existing modules; adds no strategy and no clock.

Everything produced here is SIMULATED paper output. It never establishes live profitability.

Per contract, one tick does:
  quote_source.latest(contract) -> entry_rule.evaluate(quote, now)
  -> if ENTER_LONG: check_eligibility(...) -> if ok: simulate_buy_fill(...)
Every path journals one Alert and one DecisionRecord. Fills only on ENTER_LONG + PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Type

from data.quotes import OptionContract, OptionQuote, QuoteSource
from journal.records import Alert, DecisionRecord, Journal, make_alert
from paper.sim import FeeModel, PaperFill, PaperOrder, SlippageModel, OrderSide, simulate_fill
from risk.checks import RiskConfig, RiskResult, SessionState, check_eligibility
from signals.rules import Action, EntryRule, NoEntryRule

RESULT_LABEL = "SIMULATED paper output. Does not establish live profitability."

# Registry of configurable entry rules. Only the no-op rule exists; nothing is proposed here.
ENTRY_RULES: Dict[str, Type] = {
    NoEntryRule.rule_id: NoEntryRule,
}


def entry_rule_from_config(config: RiskConfig) -> EntryRule:
    """Map config.entry_rules to a rule instance. Unknown or unset falls back to NoEntryRule."""
    name = config.entry_rules
    if isinstance(name, str) and name in ENTRY_RULES:
        return ENTRY_RULES[name]()
    return NoEntryRule()


def fee_model_from_config(config: RiskConfig) -> FeeModel:
    fees = config.raw.get("fees") or {}
    return FeeModel(per_contract=float(fees.get("per_contract", 0.0)), per_order=float(fees.get("per_order", 0.0)))


def slippage_model_from_config(config: RiskConfig) -> SlippageModel:
    return SlippageModel(ticks=int(config.raw.get("slippage_ticks", 1)), tick_size=float(config.raw.get("tick_size", 0.01)))


@dataclass
class TickResult:
    now: datetime
    alerts: List[Alert] = field(default_factory=list)
    decisions: List[DecisionRecord] = field(default_factory=list)
    orders: List[PaperOrder] = field(default_factory=list)
    fills: List[PaperFill] = field(default_factory=list)
    label: str = RESULT_LABEL

    def alert_lines(self) -> List[str]:
        return [a.line() for a in self.alerts]


def run_tick(
    config: RiskConfig,
    contracts: Sequence[OptionContract],
    quote_source: QuoteSource,
    now: datetime,
    state: SessionState,
    journal: Journal,
    entry_rule: Optional[EntryRule] = None,
    quantity: int = 1,
    fees: Optional[FeeModel] = None,
    slippage: Optional[SlippageModel] = None,
) -> TickResult:
    """Evaluate one tick for each contract. Deterministic given its inputs; `now` is passed in."""
    rule = entry_rule if entry_rule is not None else entry_rule_from_config(config)
    fees = fees if fees is not None else fee_model_from_config(config)
    slippage = slippage if slippage is not None else slippage_model_from_config(config)
    result = TickResult(now=now)

    for contract in contracts:
        quote: Optional[OptionQuote] = quote_source.latest(contract)
        signal = rule.evaluate(quote, now) if quote is not None else None

        if signal is None:
            status, risk = "BLOCKED", RiskResult(False, "NO_DATA", "no quote available")
            action, reasons = Action.NONE, [risk.reason]
        elif signal.action is not Action.ENTER_LONG:
            status, risk = "BLOCKED", RiskResult(False, "NO_SIGNAL", signal.reason)
            action, reasons = signal.action, [signal.reason]
        else:
            qty = signal.quantity or quantity
            risk = check_eligibility(config, quote, now, state, qty)
            action, reasons = signal.action, [signal.reason, risk.reason]
            status = "ELIGIBLE" if risk.ok else "BLOCKED"
            if risk.ok:
                order = PaperOrder(contract, OrderSide.BUY, qty, now, reason=signal.reason)
                result.orders.append(order)
                fill = simulate_fill(order, quote, fees, slippage, now)
                if fill is None:
                    status = "UNFILLED"
                    reasons.append("simulated fill unavailable")
                else:
                    result.fills.append(fill)
                    reasons.append(f"simulated buy {fill.quantity} @ {fill.price:.2f} fees {fill.fees:.2f}")

        rule_id = signal.rule_id if signal is not None else rule.rule_id
        alert = make_alert(now, contract.symbol(), status, rule_id, quote, risk)
        decision = DecisionRecord(
            time=now,
            contract=contract.symbol(),
            action=action.value,
            rule=rule_id,
            reasons=reasons,
            risk_code=risk.code,
            quote_source=None if quote is None else quote.source,
            quote_observed_at=None if quote is None else quote.observed_at,
        )
        journal.alert(alert)
        journal.decision(decision)
        result.alerts.append(alert)
        result.decisions.append(decision)

    return result
