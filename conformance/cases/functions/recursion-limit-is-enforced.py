# tier: impl
# ref: library/sys.html#sys.setrecursionlimit
# expect:
# RecursionError
# True
import sys

old = sys.getrecursionlimit()
sys.setrecursionlimit(100)

def deep(n):
    return deep(n + 1)

try:
    deep(0)
except RecursionError:
    print("RecursionError")
sys.setrecursionlimit(old)
print(sys.getrecursionlimit() == old)
