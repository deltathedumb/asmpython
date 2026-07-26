# expect:
# [0, 1, 2]
fs = [lambda i=i: i for i in range(3)]
print([f() for f in fs])
# per-iteration default-arg capture in a comprehension gives [0,0,0] not [0,1,2].
