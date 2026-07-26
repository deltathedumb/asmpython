# expect:
# [1, -2, -3, 4]
print(sorted([-3, 1, -2, 4], key=abs))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal, a name bound to a lambda, or a top-level function ('abs' is none of these)
