# probes: xs += mutates a list, rebinds a tuple
# expect:
# [1, 2]
# (1,)
# (1, 2)
xs = [1]
xs_alias = xs
xs += [2]
print(xs_alias)

t = (1,)
t_alias = t
t += (2,)
print(t_alias)
print(t)
