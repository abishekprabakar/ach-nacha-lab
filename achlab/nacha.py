"""NACHA fixed-width file builder, parser and validator.

A NACHA file is a sequence of 94-character records:

    1  File Header
    5    Batch Header          (one per batch)
    6      Entry Detail        (one per payment)
    7        Addenda           (optional, PPD/CCD carry at most one '05' addenda)
    8    Batch Control
    9  File Control
    9999...9                   (padding so the record count is a multiple of 10)

Control records carry three independent checksums that the ODFI and the ACH
Operator recompute: the entry/addenda count, the dollar totals split by
debit/credit, and the *entry hash* — the sum of the eight-digit RDFI ids,
truncated to its rightmost ten digits. File Control also carries the block count
(records / blocking factor 10, rounded up) and the batch count.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import routing

RECORD_LEN = 94
BLOCKING_FACTOR = 10

# Transaction codes: (account type, direction, prenote?)
TXN_CODES = {
    22: ("checking", "credit", False),
    23: ("checking", "credit", True),
    27: ("checking", "debit", False),
    28: ("checking", "debit", True),
    32: ("savings", "credit", False),
    33: ("savings", "credit", True),
    37: ("savings", "debit", False),
    38: ("savings", "debit", True),
}
SERVICE_CLASS = {200: "mixed", 220: "credits only", 225: "debits only"}
SEC_CODES = {"PPD", "CCD", "WEB", "TEL", "CTX"}


def _alnum(value: str, width: int) -> str:
    """Alphanumeric field: upper-cased, left-justified, space-filled, truncated."""
    value = (value or "").upper()
    if any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError(f"non-printable character in {value!r}")
    return value[:width].ljust(width)


def _num(value: int, width: int) -> str:
    """Numeric field: right-justified, zero-filled; overflow is an error."""
    if value < 0:
        raise ValueError("numeric NACHA fields are unsigned")
    s = str(value)
    if len(s) > width:
        raise ValueError(f"{value} does not fit in {width} digits")
    return s.zfill(width)


def entry_hash(dfi_ids) -> int:
    """Sum of eight-digit RDFI ids, keeping the rightmost ten digits."""
    return sum(int(x) for x in dfi_ids) % 10**10


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    txn_code: int
    routing: str          # full nine-digit RDFI routing number
    account: str
    amount_cents: int
    individual_id: str
    name: str
    addenda: str | None = None

    def __post_init__(self):
        if self.txn_code not in TXN_CODES:
            raise ValueError(f"unsupported transaction code {self.txn_code}")
        routing.split(self.routing)  # raises on a bad check digit
        if TXN_CODES[self.txn_code][2] and self.amount_cents != 0:
            raise ValueError("prenotification entries must carry a zero amount")
        if self.amount_cents < 0:
            raise ValueError("amounts are unsigned; direction comes from the txn code")
        if not self.account or len(self.account) > 17:
            raise ValueError("DFI account number must be 1-17 characters")

    @property
    def is_debit(self) -> bool:
        return TXN_CODES[self.txn_code][1] == "debit"


@dataclass
class Batch:
    company_name: str
    company_id: str
    sec_code: str
    entry_description: str
    effective_date: dt.date
    entries: list[Entry] = field(default_factory=list)
    discretionary: str = ""
    descriptive_date: str = ""

    def __post_init__(self):
        if self.sec_code not in SEC_CODES:
            raise ValueError(f"unsupported SEC code {self.sec_code}")

    @property
    def service_class(self) -> int:
        dirs = {e.is_debit for e in self.entries}
        if dirs == {True}:
            return 225
        if dirs == {False}:
            return 220
        return 200


def build_file(
    batches: list[Batch],
    *,
    destination_routing: str,
    destination_name: str,
    origin_id: str,
    origin_name: str,
    odfi_routing: str,
    created: dt.datetime,
    file_id_modifier: str = "A",
    reference: str = "",
) -> str:
    """Render batches into a complete, padded NACHA file (lines joined by \\n)."""
    if not (len(file_id_modifier) == 1 and file_id_modifier.isalnum()):
        raise ValueError("file ID modifier must be a single A-Z/0-9 character")
    odfi8, _ = routing.split(odfi_routing)
    routing.split(destination_routing)
    lines: list[str] = []
    lines.append(
        "1" + "01"
        + " " + destination_routing
        + _alnum(origin_id, 10)
        + created.strftime("%y%m%d") + created.strftime("%H%M")
        + file_id_modifier.upper() + "094" + "10" + "1"
        + _alnum(destination_name, 23) + _alnum(origin_name, 23)
        + _alnum(reference, 8)
    )
    file_count = file_debit = file_credit = 0
    file_hash_parts: list[int] = []
    trace_seq = 0
    for batch_no, b in enumerate(batches, start=1):
        if not b.entries:
            raise ValueError(f"batch {batch_no} has no entries")
        sc = b.service_class
        lines.append(
            "5" + _num(sc, 3) + _alnum(b.company_name, 16) + _alnum(b.discretionary, 20)
            + _alnum(b.company_id, 10) + b.sec_code + _alnum(b.entry_description, 10)
            + _alnum(b.descriptive_date, 6) + b.effective_date.strftime("%y%m%d")
            + "   " + "1" + odfi8 + _num(batch_no, 7)
        )
        count = debit = credit = 0
        dfi_ids = []
        for e in b.entries:
            trace_seq += 1
            dfi8, chk = routing.split(e.routing)
            trace = odfi8 + _num(trace_seq, 7)
            lines.append(
                "6" + _num(e.txn_code, 2) + dfi8 + chk + _alnum(e.account, 17)
                + _num(e.amount_cents, 10) + _alnum(e.individual_id, 15)
                + _alnum(e.name, 22) + "  " + ("1" if e.addenda else "0") + trace
            )
            count += 1
            dfi_ids.append(dfi8)
            if e.is_debit:
                debit += e.amount_cents
            else:
                credit += e.amount_cents
            if e.addenda:
                lines.append("7" + "05" + _alnum(e.addenda, 80) + "0001" + trace[-7:])
                count += 1
        h = entry_hash(dfi_ids)
        lines.append(
            "8" + _num(sc, 3) + _num(count, 6) + _num(h, 10) + _num(debit, 12)
            + _num(credit, 12) + _alnum(b.company_id, 10) + " " * 19 + " " * 6
            + odfi8 + _num(batch_no, 7)
        )
        file_count += count
        file_debit += debit
        file_credit += credit
        file_hash_parts.append(h)
    n_records = len(lines) + 1
    blocks = -(-n_records // BLOCKING_FACTOR)
    lines.append(
        "9" + _num(len(batches), 6) + _num(blocks, 6) + _num(file_count, 8)
        + _num(sum(file_hash_parts) % 10**10, 10) + _num(file_debit, 12)
        + _num(file_credit, 12) + " " * 39
    )
    lines.extend(["9" * RECORD_LEN] * (blocks * BLOCKING_FACTOR - len(lines)))
    for ln in lines:
        assert len(ln) == RECORD_LEN, (len(ln), ln)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

@dataclass
class ParsedEntry:
    line: int
    txn_code: int
    dfi8: str
    check: str
    account: str
    amount_cents: int
    name: str
    trace: str
    addenda: str | None = None

    @property
    def routing(self) -> str:
        return self.dfi8 + self.check

    @property
    def is_debit(self) -> bool:
        return TXN_CODES.get(self.txn_code, ("", "credit", False))[1] == "debit"


@dataclass
class ParsedBatch:
    line: int
    service_class: int
    company_name: str
    company_id: str
    sec_code: str
    effective_date: str
    batch_number: int
    entries: list[ParsedEntry] = field(default_factory=list)


@dataclass
class Report:
    batches: list[ParsedBatch]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total_debit(self) -> int:
        return sum(e.amount_cents for b in self.batches for e in b.entries if e.is_debit)

    @property
    def total_credit(self) -> int:
        return sum(e.amount_cents for b in self.batches for e in b.entries if not e.is_debit)


def _int(s: str, what: str, lineno: int, errors: list[str]) -> int:
    if not s.isdigit():
        errors.append(f"line {lineno}: {what} is not numeric: {s!r}")
        return 0
    return int(s)


def parse(text: str) -> Report:
    """Parse a NACHA file and recompute every control total it claims.

    Never raises on malformed input: every problem becomes a line in
    `Report.errors`, so a single pass reports all issues at once.
    """
    errors: list[str] = []
    batches: list[ParsedBatch] = []
    raw = text.split("\n")
    if raw and raw[-1] == "":
        raw.pop()
    current: ParsedBatch | None = None
    seen_header = seen_control = False
    file_counts = {"count": 0, "debit": 0, "credit": 0, "hash": 0}
    for i, ln in enumerate(raw, start=1):
        ln = ln.rstrip("\r")
        if len(ln) != RECORD_LEN:
            errors.append(f"line {i}: record is {len(ln)} characters, expected 94")
            continue
        kind = ln[0]
        if seen_control:
            if ln != "9" * RECORD_LEN:
                errors.append(f"line {i}: data after File Control must be all-9 padding")
            continue
        if kind == "1":
            if i != 1:
                errors.append(f"line {i}: File Header must be the first record")
            seen_header = True
            if ln[34:37] != "094" or ln[37:39] != "10":
                errors.append(f"line {i}: record size/blocking factor must be 094/10")
            if not routing.is_valid(ln[4:13]):
                errors.append(f"line {i}: immediate destination {ln[4:13]} fails check digit")
        elif kind == "5":
            if current is not None:
                errors.append(f"line {i}: Batch Header before previous batch was closed")
            current = ParsedBatch(
                line=i, service_class=_int(ln[1:4], "service class", i, errors),
                company_name=ln[4:20].rstrip(), company_id=ln[40:50].rstrip(),
                sec_code=ln[50:53], effective_date=ln[69:75],
                batch_number=_int(ln[87:94], "batch number", i, errors),
            )
            if current.service_class not in SERVICE_CLASS:
                errors.append(f"line {i}: unknown service class code {ln[1:4]}")
            try:
                dt.datetime.strptime(ln[69:75], "%y%m%d")
            except ValueError:
                errors.append(f"line {i}: effective entry date {ln[69:75]!r} is not YYMMDD")
        elif kind == "6":
            if current is None:
                errors.append(f"line {i}: Entry Detail outside a batch")
                continue
            code = _int(ln[1:3], "transaction code", i, errors)
            e = ParsedEntry(
                line=i, txn_code=code, dfi8=ln[3:11], check=ln[11],
                account=ln[12:29].rstrip(),
                amount_cents=_int(ln[29:39], "amount", i, errors),
                name=ln[54:76].rstrip(), trace=ln[79:94],
            )
            if code not in TXN_CODES:
                errors.append(f"line {i}: unsupported transaction code {ln[1:3]}")
            elif TXN_CODES[code][2] and e.amount_cents:
                errors.append(f"line {i}: prenote (code {code}) must have zero amount")
            if not routing.is_valid(e.routing):
                errors.append(f"line {i}: RDFI routing {e.routing} fails check digit")
            if current.service_class == 220 and e.is_debit:
                errors.append(f"line {i}: debit entry in credits-only (220) batch")
            if current.service_class == 225 and not e.is_debit:
                errors.append(f"line {i}: credit entry in debits-only (225) batch")
            current.entries.append(e)
        elif kind == "7":
            if current is None or not current.entries:
                errors.append(f"line {i}: Addenda without a preceding entry")
                continue
            prev = current.entries[-1]
            if prev.addenda is not None:
                errors.append(f"line {i}: more than one addenda for a {current.sec_code} entry")
            if ln[87:94] != prev.trace[-7:]:
                errors.append(f"line {i}: addenda sequence {ln[87:94]} != entry trace {prev.trace[-7:]}")
            prev.addenda = ln[3:83].rstrip()
        elif kind == "8":
            if current is None:
                errors.append(f"line {i}: Batch Control without a Batch Header")
                continue
            b = current
            count = len(b.entries) + sum(1 for e in b.entries if e.addenda is not None)
            debit = sum(e.amount_cents for e in b.entries if e.is_debit)
            credit = sum(e.amount_cents for e in b.entries if not e.is_debit)
            h = entry_hash(e.dfi8 for e in b.entries if e.dfi8.isdigit())
            claims = {
                "service class": (_int(ln[1:4], "service class", i, errors), b.service_class),
                "entry/addenda count": (_int(ln[4:10], "count", i, errors), count),
                "entry hash": (_int(ln[10:20], "entry hash", i, errors), h),
                "total debit": (_int(ln[20:32], "debit total", i, errors), debit),
                "total credit": (_int(ln[32:44], "credit total", i, errors), credit),
                "batch number": (_int(ln[87:94], "batch number", i, errors), b.batch_number),
            }
            for what, (claimed, actual) in claims.items():
                if claimed != actual:
                    errors.append(f"line {i}: batch {b.batch_number} {what} says {claimed}, recomputed {actual}")
            file_counts["count"] += count
            file_counts["debit"] += debit
            file_counts["credit"] += credit
            file_counts["hash"] += h
            batches.append(b)
            current = None
        elif kind == "9":
            if current is not None:
                errors.append(f"line {i}: File Control while batch {current.batch_number} is open")
            seen_control = True
            n_records = i
            blocks = -(-len(raw) // BLOCKING_FACTOR)
            claims = {
                "batch count": (_int(ln[1:7], "batch count", i, errors), len(batches)),
                "block count": (_int(ln[7:13], "block count", i, errors), -(-n_records // BLOCKING_FACTOR)),
                "entry/addenda count": (_int(ln[13:21], "count", i, errors), file_counts["count"]),
                "entry hash": (_int(ln[21:31], "entry hash", i, errors), file_counts["hash"] % 10**10),
                "total debit": (_int(ln[31:43], "debit total", i, errors), file_counts["debit"]),
                "total credit": (_int(ln[43:55], "credit total", i, errors), file_counts["credit"]),
            }
            for what, (claimed, actual) in claims.items():
                if claimed != actual:
                    errors.append(f"line {i}: file {what} says {claimed}, recomputed {actual}")
            if blocks * BLOCKING_FACTOR != len(raw):
                errors.append(f"file has {len(raw)} records, not a multiple of the blocking factor 10")
        else:
            errors.append(f"line {i}: unknown record type {kind!r}")
    if not seen_header:
        errors.append("missing File Header (type 1)")
    if not seen_control:
        errors.append("missing File Control (type 9)")
    if current is not None:
        errors.append(f"batch {current.batch_number} never closed with a Batch Control")
    traces = [e.trace for b in batches for e in b.entries]
    if len(traces) != len(set(traces)):
        errors.append("duplicate trace numbers in file")
    return Report(batches=batches, errors=errors)
