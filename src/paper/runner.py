"""Single-tick paper runner. Orchestrates existing modules; adds no strategy and no clock.

Everything produced here is SIMULATED paper output. It never establishes live profitability.

Per contract, one tick does:
  quote_source.latest(contract) -> entry_rule.evaluate(quote, now)
  -> if ENTER_LONG: check_eligibility(...) -> if ok: simulate_buy_fill(...)
Every path journals one Alert and one DecisionRecord. Fills only on ENTER_LONG + PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from data.quotes import OptionContract, OptionQuote, QuoteSource
from journal.records import Alert, DecisionRecord, Journal, TradeRecord, make_alert
from paper.sim import (FeeModel, PaperFill, PaperOrder, RoundTripResult, SlippageModel, OrderSide,
                       round_trip_return, simulate_fill)
from risk.checks import RiskConfig, RiskResult, SessionState, check_eligibility
from signals.rules import (Action, EntryRule, ExitParams, LiquidityWindowEntry, LiquidityWindowParams,
                           NoEntryRule, ProfitTargetExit, Signal, parse_hhmm)

RESULT_LABEL = "SIMULATED paper output. Does not establish live profitability."

# Registry of configurable entry rules: rule id -> factory taking the config's rule_params dict.
# Nothing here is a profitability signal.
ENTRY_RULES: Dict[str, Callable[[Dict[str, Any]], EntryRule]] = {
    NoEntryRule.rule_id: lambda params: NoEntryRule(),
    LiquidityWindowEntry.rule_id: lambda params: LiquidityWindowEntry(LiquidityWindowParams.from_dict(params)),
}


def entry_rule_from_config(config: RiskConfig) -> EntryRule:
    """Map config.entry_rules to a rule instance. Unknown, unset or unbuildable falls back to NoEntryRule."""
    name = config.entry_rules
    if isinstance(name, str) and name in ENTRY_RULES:
        params = dict(config.raw.get("rule_params") or {})
        params.setdefault("session_timezone", config.session_timezone)
        try:
            return ENTRY_RULES[name](params)
        except (KeyError, ValueError, TypeError):
            return NoEntryRule()
    return NoEntryRule()


def exit_rule_from_config(config: RiskConfig, fees: Optional[FeeModel] = None,
                          slippage: Optional[SlippageModel] = None) -> ProfitTargetExit:
    params = ExitParams(
        profit_target=config.profit_target,
        stop_loss_fraction=config.stop_loss_fraction,
        latest_exit_time=parse_hhmm(config.latest_exit_time or "15:30"),
        session_timezone=config.session_timezone,
    )
    return ProfitTargetExit(params, fees or fee_model_from_config(config), slippage or slippage_model_from_config(config))


def fee_model_from_config(config: RiskConfig) -> FeeModel:
    fees = config.raw.get("fees") or {}
    return FeeModel(per_contract=float(fees.get("per_contract", 0.0)), per_order=float(fees.get("per_order", 0.0)))


def slippage_model_from_config(config: RiskConfig) -> SlippageModel:
    return SlippageModel(ticks=int(config.raw.get("slippage_ticks", 1)), tick_size=float(config.raw.get("tick_size", 0.01)))


@dataclass(frozen=True)
class ContractOutcome:
    """Everything one tick produced for one contract."""

    contract: OptionContract
    quote: Optional[OptionQuote]          # observed, may be None
    signal: Optional[Signal]              # None when there was no quote to evaluate
    risk: RiskResult
    alert: Alert
    decision: DecisionRecord
    order: Optional[PaperOrder] = None
    fill: Optional[PaperFill] = None      # simulated
    journal_entries: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TickResult:
    now: datetime
    outcomes: List[ContractOutcome] = field(default_factory=list)
    label: str = RESULT_LABEL

    @property
    def alerts(self) -> List[Alert]:
        return [o.alert for o in self.outcomes]

    @property
    def decisions(self) -> List[DecisionRecord]:
        return [o.decision for o in self.outcomes]

    @property
    def orders(self) -> List[PaperOrder]:
        return [o.order for o in self.outcomes if o.order is not None]

    @property
    def fills(self) -> List[PaperFill]:
        return [o.fill for o in self.outcomes if o.fill is not None]

    @property
    def journal_entries(self) -> List[Dict[str, Any]]:
        return [e for o in self.outcomes for e in o.journal_entries]

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
        order: Optional[PaperOrder] = None
        fill: Optional[PaperFill] = None

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
                fill = simulate_fill(order, quote, fees, slippage, now)
                if fill is None:
                    status = "UNFILLED"
                    reasons.append("simulated fill unavailable")
                else:
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
        entries = [journal.alert(alert), journal.decision(decision)]
        result.outcomes.append(ContractOutcome(
            contract=contract, quote=quote, signal=signal, risk=risk, alert=alert,
            decision=decision, order=order, fill=fill, journal_entries=entries,
        ))

    return result


@dataclass(frozen=True)
class ExitOutcome:
    """One exit evaluation for an open simulated position."""

    position: PaperFill
    quote: Optional[OptionQuote]
    signal: Signal
    alert: Alert
    decision: DecisionRecord
    exit_fill: Optional[PaperFill] = None
    result: Optional[RoundTripResult] = None
    journal_entries: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return self.exit_fill is not None


def run_exit_tick(
    position: PaperFill,
    quote_source: QuoteSource,
    now: datetime,
    journal: Journal,
    exit_rule: ProfitTargetExit,
    fees: FeeModel,
    slippage: SlippageModel,
) -> ExitOutcome:
    """Evaluate the exit rule for one open position and, on EXIT, simulate the sell fill."""
    quote = quote_source.latest(position.contract)
    signal = exit_rule.evaluate(position, quote, now)
    exit_fill: Optional[PaperFill] = None
    result: Optional[RoundTripResult] = None
    reasons = [signal.reason]

    if signal.action is Action.EXIT:
        if quote is not None and quote.is_two_sided:
            order = PaperOrder(position.contract, OrderSide.SELL, position.quantity, now, reason=signal.reason)
            exit_fill = simulate_fill(order, quote, fees, slippage, now)
        if exit_fill is None or exit_fill.quantity != position.quantity:
            exit_fill = None
            status = "EXIT_PENDING"
            risk = RiskResult(False, "NO_DATA", "exit signaled but no full simulated sell fill available")
            reasons.append(risk.reason)
        else:
            result = round_trip_return(position, exit_fill)
            status = "EXIT"
            risk = RiskResult(True, "OK", f"simulated sell {exit_fill.quantity} @ {exit_fill.price:.2f}")
            reasons.append(f"net {result.net_pnl:.2f} ({result.return_on_premium:.2%}) incl. fees {result.total_fees:.2f}")
    else:
        status = "HOLD"
        risk = RiskResult(True, "OK", "holding")

    alert = make_alert(now, position.contract.symbol(), status, signal.rule_id, quote, risk)
    decision = DecisionRecord(
        time=now, contract=position.contract.symbol(), action=signal.action.value, rule=signal.rule_id,
        reasons=reasons, risk_code=risk.code,
        quote_source=None if quote is None else quote.source,
        quote_observed_at=None if quote is None else quote.observed_at,
    )
    entries = [journal.alert(alert), journal.decision(decision)]
    if exit_fill is not None and result is not None:
        entries.append(journal.trade(TradeRecord.from_fills(
            position, exit_fill, net_pnl=result.net_pnl, return_on_premium=result.return_on_premium,
            exit_reason=signal.reason,
        )))
    return ExitOutcome(position, quote, signal, alert, decision, exit_fill, result, entries)


def apply_entry(state: SessionState, fill: PaperFill) -> SessionState:
    return replace(state, open_positions=state.open_positions + 1, entries_this_session=state.entries_this_session + 1)


def apply_exit(state: SessionState, result: RoundTripResult) -> SessionState:
    return replace(state, open_positions=max(0, state.open_positions - 1),
                   realized_pnl=round(state.realized_pnl + result.net_pnl, 4))
