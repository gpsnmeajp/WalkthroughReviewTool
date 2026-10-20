from pricing import calculate_total


def checkout(prices, discount=0):
    """Calculate the payable total after a flat discount."""
    if discount < 0:
        raise ValueError("discount must be non-negative")
    subtotal = calculate_total(prices)
    return max(0, subtotal - discount)


if __name__ == "__main__":
    print(checkout([120, 80], discount=30))
