# probe57: recursive algorithms
def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

for i in range(10):
    print(fib(i), end=" ")
print()  # 0 1 1 2 3 5 8 13 21 34

def quicksort(arr: list[int]) -> list[int]:
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    mid = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    result = quicksort(left) + mid + quicksort(right)
    return result

nums = [3, 6, 8, 10, 1, 2, 1]
print(quicksort(nums))  # [1, 1, 2, 3, 6, 8, 10]

# Memoized with dict
memo: dict[str, int] = {}
def fib_memo(n: int) -> int:
    key = str(n)
    if key in memo:
        return memo[key]
    if n <= 1:
        return n
    result = fib_memo(n - 1) + fib_memo(n - 2)
    memo[key] = result
    return result

print(fib_memo(30))  # 832040
