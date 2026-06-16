# probe: more complex patterns

# 1. Recursion with multiple base cases
def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

print(fib(10))

# 2. List of lists
matrix = [[0] * 3 for _ in range(3)]
matrix[0][0] = 1
matrix[1][1] = 2
matrix[2][2] = 3
print(matrix[1][1])
print(matrix[0][2])

# 3. Dict of lists
d: dict[str, list[int]] = {}
d["evens"] = [2, 4, 6]
d["odds"] = [1, 3, 5]
print(d["evens"][1])
print(len(d["odds"]))

# 4. String multiplication and joining
sep = "-" * 5
print(sep)
parts = ["a", "b", "c"]
print(",".join(parts))

# 5. abs() on negative
print(abs(-42))
print(abs(42))
