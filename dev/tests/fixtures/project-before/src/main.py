from pricing import calculate_total


def checkout(prices):
    return calculate_total(prices)


if __name__ == "__main__":
    print(checkout([120, 80]))
