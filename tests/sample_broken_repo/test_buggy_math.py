"""Unit tests for buggy_math fixture."""

import pytest
from buggy_math import compute_tax, safe_divide


def test_safe_divide_normal():
    assert safe_divide(10.0, 2.0) == 5.0


def test_safe_divide_by_zero():
    # Intentionally fails prior to AutoFix-CI patch
    assert safe_divide(10.0, 0.0) == 0.0


def test_compute_tax():
    assert compute_tax(100.0, 0.1) == 10.0
