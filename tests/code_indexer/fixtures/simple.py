"""Simple module for testing."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(x, y):
    result = x * y
    return result


class Calculator:
    """A simple calculator."""

    def __init__(self, name: str):
        self.name = name

    def compute(self, x: int, y: int) -> int:
        total = add(x, y)
        return multiply(total, 2)
