# probes: list.pop returns the removed element (str elements)
# expect:
# dd
# aa
# ['bb', 'cc']
xs = list(["aa", "bb", "cc", "dd"])
print(xs.pop())
print(xs.pop(0))
print(xs)
