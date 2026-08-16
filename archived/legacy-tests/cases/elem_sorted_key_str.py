# probes: sorted(key=) reads each element (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print(sorted(xs, key=str))
