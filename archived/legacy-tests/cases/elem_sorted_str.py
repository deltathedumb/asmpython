# probes: sorted() orders the elements (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
# ['dd', 'cc', 'bb', 'aa']
xs = ["aa", "bb", "cc", "dd"]
print(sorted(xs))
print(sorted(xs, reverse=True))
