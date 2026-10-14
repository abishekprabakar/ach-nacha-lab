# ach-nacha-lab

An ACH origination and operations toolkit in pure-stdlib Python: build NACHA
files byte-for-byte, validate any NACHA file by recomputing every control total
it claims, schedule settlement against the Federal Reserve's calendar and the
three Same Day ACH windows, and work returns — deadlines, type-99 return
addenda, and Originator return-rate monitoring against the Nacha thresholds.

## What's inside

| Module | What it does |
|---|---|
| `achlab/routing.py` | ABA routing check digit (3-7-1 weights, mod 10), Fed prefix ranges, split into 8-digit DFI id + check digit |
| `achlab/nacha.py` | Builder for File Header / Batch Header / Entry / Addenda / Batch Control / File Control, blocking-factor-10 padding; parser that re-derives entry counts, debit/credit totals, entry hash (rightmost 10 digits), batch and block counts, and checks service-class consistency, prenotes, addenda sequence numbers and duplicate traces — reporting *every* problem in one pass |
| `achlab/banking.py` | Fed holiday calendar with the Fed's own observance rule (Sunday → Monday, **Saturday not observed**), banking-day arithmetic, Same Day ACH window selection (10:30 / 14:45 / 16:45 ET), $1M per-entry limit, IAT exclusion, next-day and future-dated settlement |
| `achlab/returns.py` | Return reason codes, 2-banking-day vs 60-calendar-day (R05/R07/R10/R11) deadlines, return transaction codes (22→21, 27→26 …), type-99 return addenda record, unauthorized 0.5% / administrative 3% / overall 15% rate monitoring with exact-integer threshold tests |
| `achlab/__main__.py` | End-to-end demo and a `validate` command |

## Run it

```bash
python -m achlab                         # demo: writes demo/PAYROLL.ach, prints everything below
python -m achlab validate demo/PAYROLL.ach   # exit 0 if clean, 1 with a list of errors otherwise
python -m unittest discover -s tests -v  # or: pytest
```

Python 3.10+; no third-party dependencies.

## Sample output (real run)

```
== Built demo/PAYROLL.ach (30 records)

  101 01100001512345678902611241502A094101FEDERAL RESERVE BANK   ACME WIDGETS INC               
  5220ACME WIDGETS                        1234567890PPDPAYROLL   NOV 25261125   1026009590000001
  6320210000215942859575       0000175315EMP0000        EMPLOYEE 0              0026009590000001
  6220110000153301595691       0000341726EMP0001        EMPLOYEE 1              0026009590000002
  6221210002483503055453       0000416042EMP0002        EMPLOYEE 2              0026009590000003
  6320260095931922121676       0000195061EMP0003        EMPLOYEE 3              0026009590000004
  6221110000257157461338       0000186624EMP0004        EMPLOYEE 4              0026009590000005
  6220710000132033639716       0000438907EMP0005        EMPLOYEE 5              0026009590000006
  822000000600361009890000000000000000017536751234567890                         026009590000001
  5225ACME WIDGETS                        1234567890WEBSUBSCRIPT       261125   1026009590000002
  62712100024888000000         0000001999CUST00000      CUSTOMER 0              1026009590000007
  705INVOICE 2026-11-000 MONTHLY PLAN                                                00010000007
  ...

== Validate the clean file
demo/PAYROLL.ach: 2 batches, 11 entries, debits $139.96, credits $17,536.75
  OK

== Validate a copy with one payroll amount nudged by $0.01
demo/TAMPERED.ach: 2 batches, 11 entries, debits $139.96, credits $17,536.76
  ERROR line 9: batch 1 total credit says 1753675, recomputed 1753676
  ERROR line 21: file total credit says 1753675, recomputed 1753676
  2 error(s)

== Settlement timing (ET submission, Same Day ACH windows)
  submitted Wed 11-25 10:15 eff 11-25 $    2,500.00     -> settles Wed 2026-11-25 [SD1]
  submitted Wed 11-25 16:40 eff 11-25 $    2,500.00     -> settles Wed 2026-11-25 [SD3]
  submitted Wed 11-25 17:05 eff 11-25 $    2,500.00     -> settles Fri 2026-11-27 [next-day]
  submitted Wed 11-25 09:00 eff 11-25 $2,500,000.00     -> settles Fri 2026-11-27 [next-day]
  submitted Wed 11-25 09:00 eff 11-25 $    2,500.00 IAT -> settles Fri 2026-11-27 [next-day]
  submitted Fri 11-20 12:00 eff 11-25 $    2,500.00     -> settles Wed 2026-11-25 [future-dated]
  submitted Wed 12-23 12:00 eff 12-25 $    2,500.00     -> settles Mon 2026-12-28 [future-dated]

== Return deadlines for entries settled Wed 2026-11-25
  R01 Insufficient Funds                             -> Mon 2026-11-30
  R03 No Account/Unable to Locate Account            -> Mon 2026-11-30
  R29 Corporate Customer Advises Not Authorized      -> Mon 2026-11-30
  R10 Customer Advises Originator is Not Known and/o -> Mon 2027-01-25
  R07 Authorization Revoked by Customer              -> Mon 2027-01-25

== Originator return-rate monitoring (60-day window)
  healthy    4000 debits: unauthorized 0.300%, administrative 0.750%, overall 4.050%  breaches=none
  watch list 4000 debits: unauthorized 0.525%, administrative 3.250%, overall 11.275%  breaches=['unauthorized', 'administrative']

== Return entry for the first WEB debit (R01)
  original: code 27 trace 026009590000007 $19.99 -> return code 26
  799R01026009590000007      12100024                                            021000020000001
```

The one-cent tamper is caught twice — by the batch credit total and the file
credit total — while the entry hash stays clean, because the hash only covers
routing ids. Rerouting an entry instead trips the check digit *and* the hash
(see `test_rerouted_entry_breaks_check_digit_and_hash`).

## Rules encoded

* **Records** are 94 characters, blocked in tens; padding is all-`9` records.
  Service class 220 = credits only, 225 = debits only, 200 = mixed.
* **Entry hash** = Σ of the eight-digit RDFI ids in the batch (file: Σ of batch
  hashes), keeping the rightmost ten digits.
* **Fed holidays**: New Year's, MLK, Washington's Birthday, Memorial,
  Juneteenth, Independence, Labor, Columbus, Veterans, Thanksgiving, Christmas.
  On a Saturday the Fed stays open the Friday before, so 2026 has only ten
  observed holidays (July 4, 2026 is a Saturday).
* **Same Day ACH**: submit by 10:30 / 14:45 / 16:45 ET to settle at 13:00 /
  17:00 / 18:00; ≤ $1,000,000.00 per entry; no IAT.
* **Return deadlines**: second banking day after settlement; 60 calendar days
  (rolled to a banking day) for consumer unauthorized returns R05, R07, R10,
  R11. R29 stays on the two-day clock.
* **Return-rate thresholds** fire when a rate *exceeds* 0.5% / 3% / 15%.

## Tests

39 `unittest` tests (pytest-compatible). Expected values are hand-derived:
check digits worked out from the weights in comments, entry hashes summed by
hand, holiday dates taken from the Fed's published schedule and its Saturday
rule, deadlines counted on a calendar, and each return-rate threshold probed
exactly on and one return past the edge. A ten-mutant run (flip the Saturday
rule, drop the hash truncation, make the $1M limit exclusive, `>`→`>=` on rates,
1-day return clock, floor block count, wrong weights, 59-day window, exclusive
window deadline, disable the 220-batch debit check) — all 10 are caught.

## Scope

Simplifications: Same Day windows assume the submission timestamp is already in
Eastern time; returns of returns, dishonors, NOCs (COR) and IAT/CTX addenda
layouts are not modelled. This is an educational model, not a certified Nacha
implementation.

## License

MIT
