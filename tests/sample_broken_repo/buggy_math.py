"""Sample math utilities containing an intentional bug for CI self-healing verification."""


def safe_divide(a: float, b: float) -> float:
    """Safely divides a by b, returning 0.0 when denominator is zero."""
    return a / b


def compute_tax(subtotal: float, rate: float = 0.05) -> float:
    """Computes tax on subtotal."""
    return subtotal * rate
