# probes: a slice copies a run of elements (str elements)
# expect:
# ['bb', 'cc']
# ['aa', 'bb']
# ['cc', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print(xs[1:3])
print(xs[:2])
print(xs[-2:])
