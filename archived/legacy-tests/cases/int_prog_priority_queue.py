# expect:
# a b
class PQ:
    def __init__(self):
        self.items = []
    def push(self, priority, item):
        self.items.append((priority, item))
        self.items.sort(key=lambda x: x[0])
    def pop(self):
        return self.items.pop(0)[1]
pq = PQ()
pq.push(3, 'c')
pq.push(1, 'a')
pq.push(2, 'b')
print(pq.pop(), pq.pop())
# asmpython (beta/3.14.0) MISMATCH: prints '5368750122 5368750124\n' (wrong).
