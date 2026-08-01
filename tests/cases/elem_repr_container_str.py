# probes: the container renders its own elements (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
# ['aa', 'bb', 'cc', 'dd']
# ['aa', 'bb', 'cc', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print(xs)
print(repr(xs))
print(str(xs))
