def calculate_total(prices):
    """Sum non-negative prices in a list."""
    if any(price < 0 for price in prices):
        raise ValueError("prices must be non-negative")
    return sum(prices)
