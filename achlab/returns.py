"""ACH returns: reason codes, return deadlines, return entries and rate monitoring.

Deadlines (Nacha Operating Rules, Article Three, Subsection 3.8.3):

* The general rule: the RDFI must make the return available to the ODFI by the
  opening of business on the **second banking day** after the Settlement Date
  of the original entry.
* Extended return entries — a consumer account holder's Written Statement of
  Unauthorized Debit (R05, R07, R10, R11) — may be returned up to **60
  calendar days** after the Settlement Date. The 60th day is measured in
  calendar days and then rolled to a banking day.
* R29 (corporate customer advises not authorized) stays on the 2-banking-day
  clock even though it is an "unauthorized" code.

Return-rate monitoring (Subsection 2.17.2): an Originator's debit returns,
measured over the preceding 60 days or two calendar months against its debit
entries over the same period, triggers ODFI reporting when it exceeds

    unauthorized (R05, R07, R10, R11, R29, R51)   0.5 %
    administrative (R02, R03, R04)                3.0 %
    overall (every return reason)                15.0 %

Returns of a return, dishonors and RCK are out of scope here.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import banking

REASONS = {
    "R01": "Insufficient Funds",
    "R02": "Account Closed",
    "R03": "No Account/Unable to Locate Account",
    "R04": "Invalid Account Number Structure",
    "R05": "Unauthorized Debit to Consumer Account Using Corporate SEC Code",
    "R06": "Returned per ODFI's Request",
    "R07": "Authorization Revoked by Customer",
    "R08": "Payment Stopped",
    "R09": "Uncollected Funds",
    "R10": "Customer Advises Originator is Not Known and/or Not Authorized",
    "R11": "Customer Advises Entry Not in Accordance with the Terms of the Authorization",
    "R16": "Account Frozen/Entry Returned per OFAC Instruction",
    "R20": "Non-Transaction Account",
    "R23": "Credit Entry Refused by Receiver",
    "R24": "Duplicate Entry",
    "R29": "Corporate Customer Advises Not Authorized",
    "R51": "Item Related to RCK Entry is Ineligible or RCK Entry is Improper",
}
EXTENDED = frozenset({"R05", "R07", "R10", "R11"})
UNAUTHORIZED = frozenset({"R05", "R07", "R10", "R11", "R29", "R51"})
ADMINISTRATIVE = frozenset({"R02", "R03", "R04"})
THRESHOLDS = {"unauthorized": 0.005, "administrative": 0.03, "overall": 0.15}
THRESHOLD_PER_MILLE = {"unauthorized": 5, "administrative": 30, "overall": 150}

# Forward transaction code -> return / NOC code for the same account type.
RETURN_TXN = {22: 21, 23: 21, 27: 26, 28: 26, 32: 31, 33: 31, 37: 36, 38: 36}


def return_deadline(reason: str, settlement_date: dt.date) -> dt.date:
    """Last date the RDFI's return can be made available to the ODFI."""
    if reason not in REASONS:
        raise ValueError(f"unknown return reason {reason}")
    if reason in EXTENDED:
        return banking.roll_forward(settlement_date + dt.timedelta(days=60))
    return banking.add_banking_days(settlement_date, 2)


def is_timely(reason: str, settlement_date: dt.date, returned_on: dt.date) -> bool:
    return returned_on <= return_deadline(reason, settlement_date)


def return_addenda(reason: str, original_trace: str, original_rdfi8: str,
                   info: str = "", *, trace: str, death_date: str = "") -> str:
    """Build a 94-character type-99 return addenda record.

    Layout: '7' | '99' | reason(3) | original trace(15) | date of death(6) |
    original receiving DFI id(8) | addenda information(44) | trace number(15).
    """
    if reason not in REASONS:
        raise ValueError(f"unknown return reason {reason}")
    if len(original_trace) != 15 or not original_trace.isdigit():
        raise ValueError("original trace must be 15 digits")
    if len(original_rdfi8) != 8 or not original_rdfi8.isdigit():
        raise ValueError("original RDFI id must be 8 digits")
    rec = ("7" + "99" + reason + original_trace + death_date.ljust(6)[:6]
           + original_rdfi8 + info.upper().ljust(44)[:44] + trace)
    if len(rec) != 94:
        raise ValueError("trace number must be 15 characters")
    return rec


@dataclass(frozen=True)
class RateReport:
    debit_entries: int
    returns: dict[str, int]
    rates: dict[str, float]
    breaches: tuple[str, ...]


def return_rates(debit_entries: int, returns: list[str]) -> RateReport:
    """Classify debit returns and compare each rate to its Nacha threshold.

    The rule is triggered when a rate *exceeds* its threshold, so a rate
    exactly at the threshold is not a breach. Counts are compared as exact
    integers (returns * 1000 > entries * 5 for 0.5%) to avoid float edges.
    """
    if debit_entries <= 0:
        raise ValueError("need a positive count of debit entries")
    unknown = [r for r in returns if r not in REASONS]
    if unknown:
        raise ValueError(f"unknown return reasons {sorted(set(unknown))}")
    counts = {
        "unauthorized": sum(r in UNAUTHORIZED for r in returns),
        "administrative": sum(r in ADMINISTRATIVE for r in returns),
        "overall": len(returns),
    }
    rates = {k: v / debit_entries for k, v in counts.items()}
    breaches = tuple(k for k in ("unauthorized", "administrative", "overall")
                     if counts[k] * 1000 > debit_entries * THRESHOLD_PER_MILLE[k])
    return RateReport(debit_entries, counts, rates, breaches)
