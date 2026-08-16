# expect:
# {0: 0, 2: 4, 4: 16}
print({x: x ** 2 for x in range(6) if x % 2 == 0})
# asmpython (beta/3.14.0) rejects at compile: [E054] dict comprehension keys must be strings (other key kinds work for lookup but not when the comprehension's result is iterated/printed)
