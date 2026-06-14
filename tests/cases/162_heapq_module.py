# expect:
# 1
# 2
# 3
# 10
# 1
# 2
# 3

from heapq import heappush, heappop, heapify, nsmallest

h = []
heappush(h, 5)
heappush(h, 3)
heappush(h, 1)
heappush(h, 4)
heappush(h, 2)

print(heappop(h))
print(heappop(h))
print(heappop(h))

data = [10, 3, 7, 1, 5, 2, 8]
heapify(data)
print(data[0])

small = nsmallest(3, [10, 3, 7, 1, 5])
print(small[0])
print(small[1])
print(small[2])
