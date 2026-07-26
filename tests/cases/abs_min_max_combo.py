# expect:
# -8 2
vals = [-5, 3, -8, 2]
print(max(vals, key=abs), min(vals, key=abs))
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal, a name bound to a lambda, or a top-level function ('abs' is none of these)
