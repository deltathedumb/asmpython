# expect:
# 18
# 2
# 9
# ab
# 15
nums = [1, 2, 3, 4, 5]


def total_of(xs: list) -> int:
    acc = 0
    for v in xs:
        acc = acc + v
    return acc


# Filtered + mapped list comprehension: odd n, doubled -> [2, 6, 10].
doubled = [n * 2 for n in nums if n % 2 == 1]
print(total_of(doubled))            # 18

evens = [n for n in nums if n % 2 == 0]
print(len(evens))                   # [2, 4] -> 2

squares = [n * n for n in nums]
print(squares[2])                   # 3*3 = 9

# String-typed elements.
words = ["a", "b"]
copied = [w for w in words]
print(copied[0] + copied[1])        # ab

# A generator expression handed straight to a list-consuming function.
print(total_of(n for n in nums))    # 1+2+3+4+5 = 15
