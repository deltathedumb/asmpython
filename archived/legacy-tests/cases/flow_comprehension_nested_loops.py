# probes: nested for clauses iterate left to right
# expect:
# [(1, 'x'), (1, 'y'), (2, 'x'), (2, 'y')]
print([(a, b) for a in [1, 2] for b in ["x", "y"]])
