# probes: list.sort(key=) reads each element (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
xs = list(["aa", "bb", "cc", "dd"])
xs.sort(key=str)
print(xs)
