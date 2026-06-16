# probe81: complex algorithms - fibonacci, prime sieve, BFS
# Fibonacci (iterative)
def fib(n: int) -> int:
    if n <= 1:
        return n
    a: int = 0
    b: int = 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b

for i in range(10):
    print(fib(i), end=" ")
print()   # 0 1 1 2 3 5 8 13 21 34

# Sieve of Eratosthenes
def primes(n: int) -> list:
    sieve = [True] * (n + 1)
    sieve[0] = False
    if n >= 1:
        sieve[1] = False
    i: int = 2
    while i * i <= n:
        if sieve[i]:
            j: int = i * i
            while j <= n:
                sieve[j] = False
                j = j + i
        i = i + 1
    result = []
    for k in range(n + 1):
        if sieve[k]:
            result.append(k)
    return result

ps = primes(30)
print(len(ps))     # 10
print(ps[0])       # 2
print(ps[-1])      # 29

# Binary search
def bsearch(arr: list, target: int) -> int:
    lo: int = 0
    hi: int = len(arr) - 1
    while lo <= hi:
        mid: int = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1

arr = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
print(bsearch(arr, 7))    # 3
print(bsearch(arr, 19))   # 9
print(bsearch(arr, 4))    # -1
