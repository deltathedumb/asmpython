# probe107: complex control flow - early returns, multiple paths
def classify_score(score: int) -> str:
    if score < 0:
        return "invalid"
    if score < 60:
        return "F"
    if score < 70:
        return "D"
    if score < 80:
        return "C"
    if score < 90:
        return "B"
    if score <= 100:
        return "A"
    return "invalid"

for s in [-1, 55, 65, 75, 85, 95, 101]:
    print(classify_score(s))
# invalid, F, D, C, B, A, invalid

# Complex conditional returns
def fizzbuzz(n: int) -> str:
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)

for i in range(1, 16):
    print(fizzbuzz(i), end=" ")
print()
# 1 2 Fizz 4 Buzz Fizz 7 8 Fizz Buzz 11 Fizz 13 14 FizzBuzz

# Function with complex branching
def collatz_len(n: int) -> int:
    steps: int = 0
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = n * 3 + 1
        steps = steps + 1
    return steps

print(collatz_len(1))    # 0
print(collatz_len(6))    # 8
print(collatz_len(27))   # 111
