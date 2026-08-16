# expect:
# 5
# 1
# 2
# 3
# 3
# 1
# 2

import heapq

h: list = [5, 3, 7, 1, 9]
heapq.heapify(h)
print(len(h))
print(heapq.heappop(h))
heapq.heappush(h, 2)
print(heapq.heappop(h))
print(heapq.heappop(h))

h2: list = []
heapq.heappush(h2, 9)
heapq.heappush(h2, 2)
heapq.heappush(h2, 1)
print(len(h2))
print(heapq.heappop(h2))
print(heapq.heappop(h2))
