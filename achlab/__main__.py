"""End-to-end demo: originate a payroll + collections file, validate it, catch a
corrupted copy, schedule settlement, and work a batch of returns.

    python -m achlab                # run the demo, writes demo/PAYROLL.ach
    python -m achlab validate FILE  # validate any NACHA file, exit 1 on errors
"""
from __future__ import annotations

import datetime as dt
import pathlib
import random
import sys

from . import banking, nacha, returns, routing

RDFIS = ["021000021", "011000015", "121000248", "026009593", "111000025", "071000013"]


def _validate_cli(path: str) -> int:
    report = nacha.parse(pathlib.Path(path).read_text())
    n = sum(len(b.entries) for b in report.batches)
    print(f"{path}: {len(report.batches)} batches, {n} entries, "
          f"debits ${report.total_debit/100:,.2f}, credits ${report.total_credit/100:,.2f}")
    for e in report.errors:
        print("  ERROR", e)
    print("  OK" if report.ok else f"  {len(report.errors)} error(s)")
    return 0 if report.ok else 1


def demo(outdir: pathlib.Path) -> None:
    rng = random.Random(7)
    for r in RDFIS:
        assert routing.is_valid(r), r
    eff = dt.date(2026, 11, 25)  # Wednesday before Thanksgiving
    payroll = nacha.Batch("ACME WIDGETS", "1234567890", "PPD", "PAYROLL", eff,
                          descriptive_date="NOV 25")
    for k in range(6):
        payroll.entries.append(nacha.Entry(
            22 if k % 3 else 32, RDFIS[k], f"{rng.randrange(10**9, 10**10)}",
            rng.randrange(150_000, 450_000), f"EMP{k:04d}", f"Employee {k}"))
    collections = nacha.Batch("ACME WIDGETS", "1234567890", "WEB", "SUBSCRIPT", eff)
    for k in range(4):
        collections.entries.append(nacha.Entry(
            27, RDFIS[(k + 2) % len(RDFIS)], f"88{k:06d}", 1999 + 1000 * k,
            f"CUST{k:05d}", f"Customer {k}",
            addenda=f"INVOICE 2026-11-{k:03d} MONTHLY PLAN"))
    collections.entries.append(nacha.Entry(28, "021000021", "55500011", 0,
                                           "CUST99999", "Prenote Test"))
    text = nacha.build_file(
        [payroll, collections], destination_routing="011000015",
        destination_name="FEDERAL RESERVE BANK", origin_id="1234567890",
        origin_name="ACME WIDGETS INC", odfi_routing="026009593",
        created=dt.datetime(2026, 11, 24, 15, 2))
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "PAYROLL.ach"
    path.write_text(text)
    print(f"== Built {path} ({len(text.splitlines())} records)\n")
    for ln in text.splitlines()[:12]:
        print("  " + ln)
    print("  ...\n")

    print("== Validate the clean file")
    _validate_cli(str(path))

    print("\n== Validate a copy with one payroll amount nudged by $0.01")
    lines = text.splitlines()
    i = next(j for j, ln in enumerate(lines) if ln.startswith("622"))
    amt = int(lines[i][29:39]) + 1
    lines[i] = lines[i][:29] + str(amt).zfill(10) + lines[i][39:]
    bad = outdir / "TAMPERED.ach"
    bad.write_text("\n".join(lines) + "\n")
    _validate_cli(str(bad))

    print("\n== Settlement timing (ET submission, Same Day ACH windows)")
    cases = [
        (dt.datetime(2026, 11, 25, 10, 15), eff, 250_000, False),
        (dt.datetime(2026, 11, 25, 16, 40), eff, 250_000, False),
        (dt.datetime(2026, 11, 25, 17, 5), eff, 250_000, False),
        (dt.datetime(2026, 11, 25, 9, 0), eff, 2_500_000_00, False),
        (dt.datetime(2026, 11, 25, 9, 0), eff, 250_000, True),
        (dt.datetime(2026, 11, 20, 12, 0), eff, 250_000, False),
        (dt.datetime(2026, 12, 23, 12, 0), dt.date(2026, 12, 25), 250_000, False),
    ]
    for sub, ed, amt, iat in cases:
        d, w = banking.settlement(sub, ed, amt, iat=iat)
        tag = " IAT" if iat else ""
        print(f"  submitted {sub:%a %m-%d %H:%M} eff {ed:%m-%d} ${amt/100:>12,.2f}{tag:4} "
              f"-> settles {d:%a %Y-%m-%d} [{w}]")

    print("\n== Return deadlines for entries settled Wed 2026-11-25")
    for code in ["R01", "R03", "R29", "R10", "R07"]:
        d = returns.return_deadline(code, eff)
        print(f"  {code} {returns.REASONS[code][:46]:46} -> {d:%a %Y-%m-%d}")

    print("\n== Originator return-rate monitoring (60-day window)")
    for label, n, rs in [
        ("healthy", 4000, ["R01"] * 120 + ["R02"] * 30 + ["R10"] * 12),
        ("watch list", 4000, ["R01"] * 300 + ["R03"] * 90 + ["R04"] * 40 + ["R10"] * 21),
    ]:
        rep = returns.return_rates(n, rs)
        rates = ", ".join(f"{k} {v:.3%}" for k, v in rep.rates.items())
        print(f"  {label:10} {n} debits: {rates}  breaches={list(rep.breaches) or 'none'}")

    print("\n== Return entry for the first WEB debit (R01)")
    parsed = nacha.parse(text)
    e = parsed.batches[1].entries[0]
    print(f"  original: code {e.txn_code} trace {e.trace} ${e.amount_cents/100:.2f}"
          f" -> return code {returns.RETURN_TXN[e.txn_code]}")
    print("  " + returns.return_addenda("R01", e.trace, e.dfi8, trace="021000020000001"))


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "validate":
        return max(_validate_cli(p) for p in argv[1:])
    demo(pathlib.Path("demo"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
