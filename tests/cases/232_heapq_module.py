# expect:
# 1
# 1
# 2
# 3

import heapq

h: list = [3, 1, 4, 1, 5, 9]
heapq.heapify(h)
print(heapq.heappop(h))
print(heapq.heappop(h))
heapq.heappush(h, 2)
print(heapq.heappop(h))
print(len(heapq.nsmallest(3, [5, 3, 1, 2, 4])))
