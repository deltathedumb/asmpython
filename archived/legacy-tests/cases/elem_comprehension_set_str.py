# probes: a set comprehension reads each element (str elements)
# expect:
# ['aa', 'bb', 'cc', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print(sorted({str(v) for v in xs}))
