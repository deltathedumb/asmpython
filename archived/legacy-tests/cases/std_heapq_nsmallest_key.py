# probes: nsmallest accepts a key= function
# expect:
# [(2, 'a'), (1, 'b')]
import heapq

print(heapq.nsmallest(2, [(1, "b"), (0, "c"), (2, "a")], key=lambda p: p[1]))
