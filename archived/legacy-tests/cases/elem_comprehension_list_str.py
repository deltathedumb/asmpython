# probes: a list comprehension reads each element (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print([v for v in xs])
