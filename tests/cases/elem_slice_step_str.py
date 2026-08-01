# probes: an extended slice copies with a step (str elements)
# expect:
# ['aa', 'cc']
# ['dd', 'cc', 'bb', 'aa']
# ['bb', 'dd']
xs = ["aa", "bb", "cc", "dd"]
print(xs[::2])
print(xs[::-1])
print(xs[1::2])
