"""Utility functions for the agent.

This module provides a simple `add` function used in the test suite.
"""

from __future__ import annotations


def add(a: int | float, b: int | float) -> int | float:
    """Return the sum of *a* and *b*.

    The function accepts both integers and floats and returns the same type
    as the operands.  It is intentionally simple but includes type hints
    and a docstring to aid static analysis and readability.
    """
    return a + b
