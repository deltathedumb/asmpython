# probes: set operations compare elements (str elements)
# expect:
# ['aa', 'bb', 'cc', 'ee']
# ['bb', 'cc']
# ['aa']
a = set(["aa", "bb", "cc"])
b = set(["bb", "cc", "ee"])
print(sorted(a | b, key=str))
print(sorted(a & b, key=str))
print(sorted(a - b, key=str))
