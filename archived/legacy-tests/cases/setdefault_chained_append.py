# expect:
# {'a': [1]}
d = {}
d.setdefault("a", []).append(1)
print(d)
# asmpython (beta/3.14.0): "[E113] int has no method 'append'" -- the return
# value of dict.setdefault() is not typed as the list default, so the chained
# .append() is rejected.
