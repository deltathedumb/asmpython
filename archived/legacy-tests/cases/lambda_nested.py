# expect:
# 7
add = lambda x: lambda y: x + y
print(add(3)(4))
# asmpython (beta/3.14.0) rejects at compile: [P001] unexpected token KEYWORD 'lambda'
