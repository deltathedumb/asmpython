# expect:
# 3
# 1
# 10
# 20
# 2
# 1
# 0

from collections import deque, Counter

d = deque()
d.append(10)
d.append(20)
d.append(30)
print(len(d))
d.appendleft(1)
print(d.popleft())
print(d.popleft())
print(d.popleft())

words: list[str] = ["apple", "banana", "apple"]
c = Counter(words)
print(c["apple"])
print(c["banana"])
print(c["cherry"])
