#!/usr/bin/env python3
"""Tests for the rover rotation arc-length formula fix.

The bug: line 260 of motion.py previously divided by 1000 instead of 360.

    Before (WRONG): arc_cm = abs(degrees) / 1000.0 * math.pi * WHEELBASE_CM
    After  (FIXED): arc_cm = abs(degrees) / 360.0  * math.pi * WHEELBASE_CM

Since motion.py imports lgpio and initializes GPIO hardware at module level,
we cannot import it directly in a test environment. Instead we reimplement
the pure-math formulas and verify correctness independently.
"""

import math
import unittest

# ---- Constants from motion.py (must match exactly) ----
WHEEL_DIAMETER_CM = 6.8
WHEELBASE_CM = 19.2
ENCODER_SIGNALS = 150
QUAD_MULTIPLIER = 4
COUNTS_PER_REV = ENCODER_SIGNALS * QUAD_MULTIPLIER  # 600


# ---- Reimplemented pure-math functions from motion.py ----

def _cm_to_counts(cm: float) -> float:
    """Converts a distance in cm to encoder counts."""
    return abs(cm) / (math.pi * WHEEL_DIAMETER_CM) * COUNTS_PER_REV


def _arc_cm_fixed(degrees: float) -> float:
    """FIXED formula: arc each wheel must travel for a given rotation."""
    return abs(degrees) / 360.0 * math.pi * WHEELBASE_CM


def _arc_cm_buggy(degrees: float) -> float:
    """BUGGY formula (divided by 1000 instead of 360)."""
    return abs(degrees) / 1000.0 * math.pi * WHEELBASE_CM


class TestRotationArcFormula(unittest.TestCase):
    """Validate the fixed rotation arc-length calculation."""

    def test_full_rotation_360_degrees(self):
        """A 360-degree rotation means each wheel travels exactly pi * WHEELBASE_CM."""
        arc = _arc_cm_fixed(360)
        expected = math.pi * WHEELBASE_CM  # ~60.319 cm
        self.assertAlmostEqual(arc, expected, places=6,
                               msg="360-degree arc should equal pi * wheelbase")

    def test_half_rotation_180_degrees(self):
        """180 degrees should be half of a full rotation arc."""
        arc = _arc_cm_fixed(180)
        expected = 0.5 * math.pi * WHEELBASE_CM  # ~30.159 cm
        self.assertAlmostEqual(arc, expected, places=6)

    def test_quarter_rotation_90_degrees(self):
        """90 degrees should be one quarter of a full rotation arc."""
        arc = _arc_cm_fixed(90)
        expected = 0.25 * math.pi * WHEELBASE_CM  # ~15.080 cm
        self.assertAlmostEqual(arc, expected, places=6)

    def test_zero_degrees(self):
        """Zero degrees should produce zero arc length."""
        self.assertEqual(_arc_cm_fixed(0), 0.0)

    def test_negative_degrees_uses_absolute_value(self):
        """Negative degree values should produce the same arc as positive."""
        self.assertAlmostEqual(_arc_cm_fixed(-90), _arc_cm_fixed(90), places=6)
        self.assertAlmostEqual(_arc_cm_fixed(-180), _arc_cm_fixed(180), places=6)

    def test_small_angle_10_degrees(self):
        """Small angle rotation should produce proportionally small arc."""
        arc = _arc_cm_fixed(10)
        expected = (10.0 / 360.0) * math.pi * WHEELBASE_CM
        self.assertAlmostEqual(arc, expected, places=6)

    def test_large_angle_720_degrees(self):
        """720 degrees (two full rotations) should be double the single rotation arc."""
        arc = _arc_cm_fixed(720)
        expected = 2.0 * math.pi * WHEELBASE_CM
        self.assertAlmostEqual(arc, expected, places=6)


class TestBuggyFormulaProducesWrongResults(unittest.TestCase):
    """Verify the old buggy formula gives incorrect results, confirming the fix matters."""

    def test_buggy_vs_fixed_ratio(self):
        """The buggy formula divides by 1000 instead of 360, so it produces
        360/1000 = 0.36x the correct arc length. This would make the rover
        rotate far less than intended."""
        for deg in [90, 180, 360]:
            buggy = _arc_cm_buggy(deg)
            fixed = _arc_cm_fixed(deg)
            ratio = buggy / fixed
            self.assertAlmostEqual(ratio, 360.0 / 1000.0, places=6,
                                   msg=f"At {deg} degrees, buggy/fixed ratio should be 0.36")

    def test_buggy_360_is_not_pi_times_wheelbase(self):
        """With the bug, a 360-degree command would only produce ~36% of the
        correct arc, so the rover would stop after about 130 degrees of rotation."""
        buggy_arc = _arc_cm_buggy(360)
        correct_arc = math.pi * WHEELBASE_CM
        self.assertNotAlmostEqual(buggy_arc, correct_arc, places=2,
                                  msg="Buggy formula should NOT produce correct arc for 360 degrees")
        # It should be ~36% of the correct value
        self.assertAlmostEqual(buggy_arc / correct_arc, 0.36, places=6)


class TestCmToCountsConversion(unittest.TestCase):
    """Validate the cm-to-encoder-counts conversion used downstream of the arc calc."""

    def test_one_wheel_circumference(self):
        """One full wheel revolution (pi * diameter cm) should equal exactly COUNTS_PER_REV."""
        circumference = math.pi * WHEEL_DIAMETER_CM
        counts = _cm_to_counts(circumference)
        self.assertAlmostEqual(counts, COUNTS_PER_REV, places=6,
                               msg="One wheel circumference should equal one revolution of counts")

    def test_zero_cm(self):
        """Zero distance should yield zero counts."""
        self.assertEqual(_cm_to_counts(0), 0.0)

    def test_negative_cm_uses_absolute(self):
        """Negative distances should give the same count as positive."""
        self.assertAlmostEqual(_cm_to_counts(-10), _cm_to_counts(10), places=6)


class TestEndToEndRotationCounts(unittest.TestCase):
    """Test the full pipeline: degrees -> arc_cm -> encoder counts."""

    def test_90_degree_rotation_counts(self):
        """Verify the exact encoder count target for a 90-degree rotation."""
        arc = _arc_cm_fixed(90)
        counts = _cm_to_counts(arc)

        # Manual calculation:
        # arc = 90/360 * pi * 19.2 = 0.25 * pi * 19.2 = 15.07964...
        # counts = 15.07964... / (pi * 6.8) * 600 = 15.07964... / 21.36283... * 600
        expected_arc = 0.25 * math.pi * WHEELBASE_CM
        expected_counts = expected_arc / (math.pi * WHEEL_DIAMETER_CM) * COUNTS_PER_REV
        self.assertAlmostEqual(counts, expected_counts, places=6)

    def test_360_degree_rotation_counts(self):
        """Full rotation: arc = pi * WHEELBASE, counts = WHEELBASE / WHEEL_DIAMETER * COUNTS_PER_REV."""
        arc = _arc_cm_fixed(360)
        counts = _cm_to_counts(arc)

        # For a 360 rotation, arc = pi * WHEELBASE_CM
        # counts = (pi * WHEELBASE_CM) / (pi * WHEEL_DIAMETER_CM) * 600
        #        = WHEELBASE_CM / WHEEL_DIAMETER_CM * 600
        #        = 19.2 / 6.8 * 600
        #        = 1694.117647...
        expected = WHEELBASE_CM / WHEEL_DIAMETER_CM * COUNTS_PER_REV
        self.assertAlmostEqual(counts, expected, places=6)

    def test_buggy_90_degree_would_undershoot(self):
        """With the bug, a 90-degree rotation target count is only 36% of correct.
        The rover would stop after rotating ~32.4 degrees instead of 90."""
        buggy_arc = _arc_cm_buggy(90)
        buggy_counts = _cm_to_counts(buggy_arc)

        fixed_arc = _arc_cm_fixed(90)
        fixed_counts = _cm_to_counts(fixed_arc)

        self.assertAlmostEqual(buggy_counts / fixed_counts, 0.36, places=6,
                               msg="Buggy 90-degree rotation would only achieve 36% of intended rotation")


class TestSourceCodeConsistency(unittest.TestCase):
    """Verify the actual motion.py source contains the fixed formula, not the buggy one."""

    def test_motion_py_has_fixed_formula(self):
        """Read motion.py and confirm line 260 uses / 360.0, not / 1000.0."""
        from pathlib import Path
        motion_path = Path(__file__).resolve().parent / "motion.py"
        source = motion_path.read_text(encoding="utf-8")

        # The fixed line should be present
        self.assertIn("abs(degrees) / 360.0 * math.pi * WHEELBASE_CM", source,
                      msg="motion.py should contain the fixed formula dividing by 360.0")

        # The buggy line should NOT be present
        self.assertNotIn("abs(degrees) / 1000.0 * math.pi * WHEELBASE_CM", source,
                         msg="motion.py should NOT contain the buggy formula dividing by 1000.0")


if __name__ == "__main__":
    unittest.main()
