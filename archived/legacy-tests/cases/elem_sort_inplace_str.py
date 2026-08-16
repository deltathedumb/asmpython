# probes: list.sort orders in place (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
xs = list(["aa", "bb", "cc", "dd"])
xs.sort()
print(xs)
