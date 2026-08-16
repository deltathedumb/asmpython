# expect:
# {0: {0: 0, 1: 0}, 1: {0: 0, 1: 1}}
print({i: {j: i * j for j in range(2)} for i in range(2)})
# asmpython (beta/3.14.0) rejects at compile: [E054] dict comprehension keys must be strings (other key kinds work for lookup but not when the comprehension's result is iterated/printed)
