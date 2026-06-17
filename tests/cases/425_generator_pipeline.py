# expect:
# 1 2 3 4 5
# [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]
# [0, 1, 3, 6, 10, 15, 21, 28, 36, 45]

# Take first N from a list
def take(n: int, lst: list) -> list:
    result = []
    count: int = 0
    for item in lst:
        if count >= n:
            break
        result.append(item)
        count += 1
    return result

nats = list(range(1, 100))
first5 = take(5, nats)
print(" ".join([str(v) for v in first5]))

first_10_squares = [n * n for n in range(1, 11)]
print(first_10_squares)

running = []
total: int = 0
for v in range(10):
    total += v
    running.append(total)
print(running)
