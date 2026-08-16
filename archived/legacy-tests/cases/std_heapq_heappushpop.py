# probes: heappushpop pushes then pops in one step
# expect:
# 1
# [3, 4, 5]
import heapq

heap = [1, 3, 5]
heapq.heapify(heap)
print(heapq.heappushpop(heap, 4))
print(sorted(heap))
