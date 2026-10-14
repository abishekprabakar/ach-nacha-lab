"""Fed holiday calendar, Same Day ACH windows, return deadlines and return rates.

Holiday expectations come from the Federal Reserve's published holiday
schedules (including its Saturday rule); deadlines are counted by hand on a
calendar; return-rate edges are chosen to sit exactly on and just past each
Nacha threshold.
"""
import datetime as dt
import unittest

from achlab import banking, returns

D = dt.date
T = dt.datetime


class FedCalendar(unittest.TestCase):
    def test_2026_has_ten_observed_holidays(self):
        # July 4, 2026 is a Saturday: the Fed does not observe it on Friday July 3.
        h = banking.fed_holidays(2026)
        self.assertEqual(len(h), 10)
        self.assertEqual(sorted(h), [
            D(2026, 1, 1), D(2026, 1, 19), D(2026, 2, 16), D(2026, 5, 25),
            D(2026, 6, 19), D(2026, 9, 7), D(2026, 10, 12), D(2026, 11, 11),
            D(2026, 11, 26), D(2026, 12, 25)])
        self.assertTrue(banking.is_banking_day(D(2026, 7, 3)))

    def test_sunday_holidays_move_to_monday(self):
        self.assertIn(D(2027, 7, 5), banking.fed_holidays(2027))     # July 4 2027 is Sunday
        self.assertIn(D(2023, 1, 2), banking.fed_holidays(2023))     # Jan 1 2023 is Sunday
        self.assertIn(D(2022, 6, 20), banking.fed_holidays(2022))    # Juneteenth 2022 Sunday

    def test_saturday_holidays_are_not_observed(self):
        self.assertTrue(banking.is_banking_day(D(2027, 6, 18)))      # Juneteenth 2027 Sat
        self.assertTrue(banking.is_banking_day(D(2027, 12, 24)))     # Christmas 2027 Sat
        self.assertTrue(banking.is_banking_day(D(2021, 12, 31)))     # New Year 2022 Sat

    def test_floating_holidays(self):
        h = banking.fed_holidays(2027)
        for d in [D(2027, 1, 18), D(2027, 2, 15), D(2027, 5, 31), D(2027, 9, 6),
                  D(2027, 10, 11), D(2027, 11, 25)]:
            self.assertIn(d, h)

    def test_banking_day_arithmetic(self):
        # Wed before Thanksgiving + 1 banking day skips Thursday -> Friday
        self.assertEqual(banking.next_banking_day(D(2026, 11, 25)), D(2026, 11, 27))
        self.assertEqual(banking.add_banking_days(D(2026, 12, 24), 1), D(2026, 12, 28))
        self.assertEqual(banking.roll_forward(D(2026, 12, 26)), D(2026, 12, 28))
        self.assertEqual(banking.roll_forward(D(2026, 12, 28)), D(2026, 12, 28))
        # Dec 2026: 31 days, 8 weekend days (5,6,12,13,19,20,26,27), Christmas -> 22
        self.assertEqual(banking.banking_days_between(D(2026, 11, 30), D(2026, 12, 31)), 22)


class SameDay(unittest.TestCase):
    E = D(2026, 11, 25)

    def s(self, h, m, amt=100_00, eff=None, iat=False, day=None):
        when = T.combine(day or self.E, dt.time(h, m))
        return banking.settlement(when, eff or self.E, amt, iat=iat)

    def test_window_boundaries(self):
        self.assertEqual(self.s(10, 30), (self.E, "SD1"))
        self.assertEqual(self.s(10, 31), (self.E, "SD2"))
        self.assertEqual(self.s(14, 45), (self.E, "SD2"))
        self.assertEqual(self.s(14, 46), (self.E, "SD3"))
        self.assertEqual(self.s(16, 45), (self.E, "SD3"))
        self.assertEqual(self.s(16, 46), (D(2026, 11, 27), "next-day"))

    def test_dollar_limit_is_inclusive(self):
        self.assertEqual(self.s(9, 0, amt=1_000_000_00)[1], "SD1")
        self.assertEqual(self.s(9, 0, amt=1_000_000_01)[1], "next-day")

    def test_iat_is_never_same_day(self):
        self.assertEqual(self.s(9, 0, iat=True), (D(2026, 11, 27), "next-day"))

    def test_weekend_submission(self):
        sat = D(2026, 11, 28)
        self.assertEqual(self.s(9, 0, eff=sat, day=sat), (D(2026, 11, 30), "next-day"))

    def test_future_dated_rolls_to_banking_day(self):
        self.assertEqual(self.s(12, 0, eff=D(2026, 11, 26), day=D(2026, 11, 23)),
                         (D(2026, 11, 27), "future-dated"))

    def test_stale_effective_date_takes_earliest_window(self):
        self.assertEqual(self.s(11, 0, eff=D(2026, 11, 2)), (self.E, "SD2"))


class Returns(unittest.TestCase):
    def test_two_banking_day_rule(self):
        # Settled Fri 2026-07-03 (a banking day, July 4 is Saturday) -> Mon 6, Tue 7
        self.assertEqual(returns.return_deadline("R01", D(2026, 7, 3)), D(2026, 7, 7))
        # Settled Wed 2026-11-25: Thu holiday, Fri 27 (1), Mon 30 (2)
        self.assertEqual(returns.return_deadline("R03", D(2026, 11, 25)), D(2026, 11, 30))
        self.assertEqual(returns.return_deadline("R29", D(2026, 11, 25)), D(2026, 11, 30))

    def test_sixty_calendar_day_rule(self):
        # 2026-03-02 + 60 days = 2026-05-01 (Friday)
        self.assertEqual(returns.return_deadline("R10", D(2026, 3, 2)), D(2026, 5, 1))
        # 2026-11-25 + 60 = 2027-01-24 (Sunday) -> Monday 2027-01-25
        self.assertEqual(returns.return_deadline("R07", D(2026, 11, 25)), D(2027, 1, 25))

    def test_timeliness(self):
        self.assertTrue(returns.is_timely("R01", D(2026, 11, 25), D(2026, 11, 30)))
        self.assertFalse(returns.is_timely("R01", D(2026, 11, 25), D(2026, 12, 1)))
        self.assertTrue(returns.is_timely("R10", D(2026, 11, 25), D(2027, 1, 20)))

    def test_return_addenda_layout(self):
        rec = returns.return_addenda("R03", "026009590000007", "12100024", "acct closed",
                                     trace="121000240000042")
        self.assertEqual(len(rec), 94)
        self.assertEqual(rec[:3], "799")
        self.assertEqual(rec[3:6], "R03")
        self.assertEqual(rec[6:21], "026009590000007")
        self.assertEqual(rec[27:35], "12100024")
        self.assertEqual(rec[35:79].rstrip(), "ACCT CLOSED")
        self.assertEqual(rec[79:94], "121000240000042")

    def test_return_txn_codes(self):
        self.assertEqual(returns.RETURN_TXN[22], 21)
        self.assertEqual(returns.RETURN_TXN[27], 26)
        self.assertEqual(returns.RETURN_TXN[37], 36)

    def test_rate_threshold_edges(self):
        at = returns.return_rates(1000, ["R10"] * 5)
        self.assertEqual(at.breaches, ())                       # exactly 0.5% is not "exceeds"
        over = returns.return_rates(1000, ["R10"] * 6)
        self.assertEqual(over.breaches, ("unauthorized",))
        self.assertEqual(returns.return_rates(1000, ["R02"] * 30).breaches, ())
        self.assertEqual(returns.return_rates(1000, ["R04"] * 31).breaches, ("administrative",))
        self.assertEqual(returns.return_rates(1000, ["R01"] * 150).breaches, ())
        self.assertEqual(returns.return_rates(1000, ["R01"] * 151).breaches, ("overall",))

    def test_rate_classification(self):
        rep = returns.return_rates(2000, ["R01", "R02", "R03", "R05", "R29", "R51", "R08"])
        self.assertEqual(rep.returns, {"unauthorized": 3, "administrative": 2, "overall": 7})
        self.assertAlmostEqual(rep.rates["overall"], 7 / 2000)

    def test_rejects_unknown_codes(self):
        with self.assertRaises(ValueError):
            returns.return_deadline("R99", D(2026, 1, 2))
        with self.assertRaises(ValueError):
            returns.return_rates(10, ["R01", "RXX"])


if __name__ == "__main__":
    unittest.main()
