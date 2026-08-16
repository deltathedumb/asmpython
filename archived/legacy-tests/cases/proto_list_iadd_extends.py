# probes: list += iterable extends in place
# expect:
# [1, 2, 3]
# True
xs = [1]
alias = xs
xs += [2, 3]
print(xs)
print(alias is xs)
