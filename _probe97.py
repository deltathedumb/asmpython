# probe97: complex iteration patterns - zip_longest, chain
# Manual zip_longest
def zip_longest_fill(a: list, b: list, fill: int) -> list:
    result = []
    la = len(a)
    lb = len(b)
    n: int = la if la > lb else lb
    for i in range(n):
        av: int = a[i] if i < la else fill
        bv: int = b[i] if i < lb else fill
        result.append((av, bv))
    return result

pairs = zip_longest_fill([1, 2, 3], [10, 20], 0)
for a, b in pairs:
    print(a, b)
# 1 10, 2 20, 3 0

# Manual chain
def chain(*lsts: list) -> list:
    result = []
    for lst in lsts:
        for item in lst:
            result.append(item)
    return result

chained = chain([1, 2], [3, 4], [5])
print(len(chained))   # 5
for v in chained:
    print(v, end=" ")
print()   # 1 2 3 4 5

# Sliding window
def windows(lst: list, size: int) -> list:
    result = []
    for i in range(len(lst) - size + 1):
        result.append(lst[i:i + size])
    return result

wins = windows([1, 2, 3, 4, 5], 3)
print(len(wins))      # 3
print(wins[0])        # [1, 2, 3]
print(wins[1])        # [2, 3, 4]
print(wins[2])        # [3, 4, 5]
