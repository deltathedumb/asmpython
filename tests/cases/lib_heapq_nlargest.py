# expect:
# [8, 5, 3]
import heapq
print(heapq.nlargest(3, [1, 5, 2, 8, 3]))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
