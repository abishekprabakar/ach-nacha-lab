"""Routing check digits, NACHA record layout, control totals and the validator.

Expected values are derived by hand from the published rules, not captured
from this library's output: check digits from the 3-7-1 weighting, entry
hashes from summing eight-digit DFI ids, block counts from ceil(records/10).
"""
import datetime as dt
import unittest

from achlab import nacha, routing

EFF = dt.date(2026, 11, 25)
CREATED = dt.datetime(2026, 11, 24, 15, 2)
FILE_KW = dict(destination_routing="011000015", destination_name="FRB",
               origin_id="1234567890", origin_name="ACME", odfi_routing="026009593",
               created=CREATED)


def batch(entries, sec="PPD"):
    return nacha.Batch("ACME", "1234567890", sec, "PAYROLL", EFF, entries=list(entries))


def credit(rt="021000021", amt=100_00, **kw):
    return nacha.Entry(22, rt, "123456", amt, "ID1", "JANE DOE", **kw)


def debit(rt="021000021", amt=100_00, **kw):
    return nacha.Entry(27, rt, "123456", amt, "ID1", "JANE DOE", **kw)


class Routing(unittest.TestCase):
    def test_known_check_digits(self):
        # 0210 0002: 3*(0+0+0) + 7*(2+0+2) + 1*(1+0) = 29 -> d = 1
        self.assertEqual(routing.check_digit("02100002"), 1)
        # 0110 0001: 3*(0+0+0) + 7*(1+0+1) + 1*(1+0) = 15 -> d = 5
        self.assertEqual(routing.check_digit("01100001"), 5)
        # 1210 0024: 3*(1+0+2) + 7*(2+0+4) + 1*(1+0) = 52 -> d = 8
        self.assertEqual(routing.check_digit("12100024"), 8)

    def test_validity(self):
        for good in ["021000021", "011000015", "121000248", "026009593", "111000025"]:
            self.assertTrue(routing.is_valid(good), good)
        self.assertFalse(routing.is_valid("021000022"))   # wrong check digit
        self.assertFalse(routing.is_valid("02100002"))    # 8 digits
        self.assertFalse(routing.is_valid("02100002A"))
        # 500000005 satisfies the checksum (3*5+5=20) but 50 is not an assigned prefix
        self.assertFalse(routing.is_valid("500000005"))

    def test_every_single_digit_substitution_detected(self):
        # The 3-7-1 scheme catches every single-digit substitution.
        base = "021000021"
        for pos in range(9):
            for d in "0123456789":
                if d != base[pos]:
                    mutated = base[:pos] + d + base[pos + 1:]
                    self.assertFalse(
                        sum(int(c) * w for c, w in zip(mutated, routing.WEIGHTS)) % 10 == 0,
                        mutated)


class Building(unittest.TestCase):
    def test_every_record_is_94_and_blocked_by_10(self):
        text = nacha.build_file([batch([credit()])], **FILE_KW)
        lines = text.splitlines()
        self.assertTrue(all(len(l) == 94 for l in lines))
        # header, batch header, entry, batch control, file control = 5 -> pad to 10
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[5:], ["9" * 94] * 5)
        self.assertEqual(lines[4][7:13], "000001")  # block count

    def test_exactly_ten_records_needs_no_padding(self):
        text = nacha.build_file([batch([credit()] * 6)], **FILE_KW)
        lines = text.splitlines()
        self.assertEqual(len(lines), 10)
        self.assertTrue(lines[-1].startswith("9000001000001"))

    def test_eleven_records_need_two_blocks(self):
        text = nacha.build_file([batch([credit()] * 7)], **FILE_KW)
        lines = text.splitlines()
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[10][7:13], "000002")

    def test_field_positions(self):
        e = nacha.Entry(32, "121000248", "ACCT-77", 1234_56, "EMP42", "Ann Lee")
        lines = nacha.build_file([batch([e])], **FILE_KW).splitlines()
        h, bh, ed, bc, fc = lines[:5]
        self.assertEqual(h[:3], "101")
        self.assertEqual(h[3:13], " 011000015")
        self.assertEqual(h[23:29], "261124")
        self.assertEqual(h[29:33], "1502")
        self.assertEqual(bh[1:4], "220")                 # credits only
        self.assertEqual(bh[50:53], "PPD")
        self.assertEqual(bh[69:75], "261125")
        self.assertEqual(bh[79:87], "02600959")          # ODFI id, no check digit
        self.assertEqual(ed[1:3], "32")
        self.assertEqual(ed[3:11], "12100024")
        self.assertEqual(ed[11], "8")
        self.assertEqual(ed[12:29], "ACCT-77          ")
        self.assertEqual(ed[29:39], "0000123456")
        self.assertEqual(ed[54:76], "ANN LEE".ljust(22))
        self.assertEqual(ed[79:94], "026009590000001")
        self.assertEqual(bc[10:20], "0012100024")
        self.assertEqual(bc[32:44], "000000123456")
        self.assertEqual(fc[1:7], "000001")

    def test_entry_hash_hand_sum(self):
        # 02100002 + 01100001 + 12100024 = 15300027
        es = [credit("021000021"), credit("011000015"), credit("121000248")]
        bc = nacha.build_file([batch(es)], **FILE_KW).splitlines()[5]
        self.assertTrue(bc.startswith("8220000003" + "0015300027"))

    def test_entry_hash_keeps_rightmost_ten_digits(self):
        self.assertEqual(nacha.entry_hash(["99999999"] * 200), 19_999_999_800 % 10**10)
        self.assertEqual(nacha.entry_hash(["99999999"] * 200), 9_999_999_800)

    def test_service_class_and_totals_for_mixed_batch(self):
        es = [credit(amt=500_00), debit(amt=120_00), debit(amt=80_00, addenda="INV 9")]
        lines = nacha.build_file([batch(es, "CCD")], **FILE_KW).splitlines()
        self.assertEqual(lines[1][1:4], "200")
        bc = lines[6]
        self.assertEqual(bc[4:10], "000004")              # 3 entries + 1 addenda
        self.assertEqual(bc[20:32], "000000020000")       # debits $200.00
        self.assertEqual(bc[32:44], "000000050000")       # credits $500.00
        add = lines[5]
        self.assertEqual(add[:3], "705")
        self.assertEqual(add[83:87], "0001")
        self.assertEqual(add[87:94], lines[4][87:94])
        self.assertEqual(lines[4][78], "1")               # addenda indicator

    def test_construction_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            nacha.Entry(22, "021000022", "1", 1, "", "X")          # check digit
        with self.assertRaises(ValueError):
            nacha.Entry(23, "021000021", "1", 1, "", "X")          # nonzero prenote
        with self.assertRaises(ValueError):
            nacha.Entry(99, "021000021", "1", 1, "", "X")          # txn code
        with self.assertRaises(ValueError):
            nacha.Entry(22, "021000021", "1" * 18, 1, "", "X")     # account too long
        with self.assertRaises(ValueError):
            nacha.build_file([batch([credit(amt=10**10)])], **FILE_KW)  # 10-digit amount field
        with self.assertRaises(ValueError):
            nacha.build_file([batch([])], **FILE_KW)


class Validation(unittest.TestCase):
    def setUp(self):
        es = [credit("021000021", 250_00), credit("121000248", 75_10, addenda="BONUS")]
        ds = [debit("011000015", 19_99), debit("026009593", 5_01)]
        self.text = nacha.build_file([batch(es), batch(ds, "WEB")], **FILE_KW)
        self.lines = self.text.splitlines()

    def edit(self, i, start, new):
        ln = self.lines[i]
        self.lines[i] = ln[:start] + new + ln[start + len(new):]
        return nacha.parse("\n".join(self.lines) + "\n")

    def test_clean_round_trip(self):
        r = nacha.parse(self.text)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.total_credit, 325_10)
        self.assertEqual(r.total_debit, 25_00)
        self.assertEqual([b.service_class for b in r.batches], [220, 225])
        self.assertEqual(r.batches[0].entries[1].addenda, "BONUS")
        self.assertEqual(r.batches[1].entries[0].routing, "011000015")

    def test_amount_change_breaks_batch_and_file_totals(self):
        r = self.edit(2, 29, "0000025001")
        self.assertEqual(len(r.errors), 2)
        self.assertIn("batch 1 total credit", r.errors[0])
        self.assertIn("file total credit", r.errors[1])

    def test_rerouted_entry_breaks_check_digit_and_hash(self):
        r = self.edit(2, 3, "02100003")
        msgs = " | ".join(r.errors)
        self.assertIn("fails check digit", msgs)
        self.assertIn("entry hash", msgs)

    def test_debit_in_credit_only_batch(self):
        r = self.edit(2, 1, "27")
        self.assertTrue(any("debit entry in credits-only" in e for e in r.errors))

    def test_short_record(self):
        self.lines[2] = self.lines[2][:-1]
        r = nacha.parse("\n".join(self.lines))
        self.assertTrue(any("line 3: record is 93 characters" in e for e in r.errors))

    def test_missing_padding_is_reported(self):
        trimmed = [l for l in self.lines if l != "9" * 94]
        r = nacha.parse("\n".join(trimmed))
        self.assertTrue(any("multiple of the blocking factor" in e for e in r.errors))

    def test_addenda_sequence_mismatch(self):
        idx = next(i for i, l in enumerate(self.lines) if l.startswith("705"))
        r = self.edit(idx, 87, "0000099")
        self.assertTrue(any("addenda sequence" in e for e in r.errors))

    def test_structural_errors(self):
        r = nacha.parse("")
        self.assertIn("missing File Header (type 1)", r.errors)
        self.assertIn("missing File Control (type 9)", r.errors)
        no_bc = [l for l in self.lines if not l.startswith("8")]
        r = nacha.parse("\n".join(no_bc))
        self.assertTrue(any("Batch Header before previous batch" in e for e in r.errors))

    def test_duplicate_trace(self):
        r = self.edit(3, 79, self.lines[2][79:94])
        self.assertIn("duplicate trace numbers in file", r.errors)


if __name__ == "__main__":
    unittest.main()
