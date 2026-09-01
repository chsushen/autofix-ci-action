"""Unit tests for the Log Parser module."""

import pytest
from src.parser.log_parser import LogParser


def test_parse_standard_python_traceback():
    raw_log = """
Traceback (most recent call last):
  File "src/service.py", line 45, in execute_transaction
    balance = account.get_balance()
  File "src/models.py", line 112, in get_balance
    return self.funds / self.shares
ZeroDivisionError: division by zero
"""
    parser = LogParser()
    failures = parser.parse(raw_log)

    assert len(failures) == 1
    f = failures[0]
    assert f.failing_file == "src/models.py"
    assert f.failing_line == 112
    assert f.exception_type == "ZeroDivisionError"
    assert f.exception_message == "division by zero"
    assert len(f.stack_frames) == 2


def test_parse_pytest_output():
    raw_log = """
============================= test session starts ==============================
collecting ... collected 2 items

tests/test_calc.py .F                                                    [100%]

=================================== FAILURES ===================================
______________________________ test_divide_zero _______________________________

    def test_divide_zero():
>       res = safe_divide(10, 0)

tests/test_calc.py:15: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/calc.py:8: in safe_divide
    return a / b
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    def inner():
>       return a / b
E       ZeroDivisionError: division by zero

src/calc.py:8: ZeroDivisionError
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_divide_zero - ZeroDivisionError: division by zero
========================= 1 failed, 1 passed in 0.05s ==========================
"""
    parser = LogParser()
    failures = parser.parse(raw_log)

    assert len(failures) == 1
    f = failures[0]
    # Should prioritize application source src/calc.py over tests/test_calc.py
    assert f.failing_file == "src/calc.py"
    assert f.failing_line == 8
    assert f.exception_type == "ZeroDivisionError"
    assert "division by zero" in f.exception_message


def test_parse_assertion_error():
    raw_log = """
_______________________________ test_math_sum ________________________________

    def test_math_sum():
>       assert add(2, 2) == 5
E       AssertionError: assert 4 == 5

tests/test_math.py:12: AssertionError
"""
    parser = LogParser()
    failures = parser.parse(raw_log)

    assert len(failures) == 1
    f = failures[0]
    assert f.failing_file == "tests/test_math.py"
    assert f.failing_line == 12
    assert f.exception_type == "AssertionError"


def test_empty_log_returns_empty_list():
    parser = LogParser()
    assert parser.parse("") == []
    assert parser.parse("All tests passed! 10 passed in 0.20s") == []
