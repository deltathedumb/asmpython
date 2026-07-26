# expect:
# 15
def adder(n):
    return lambda x: x + n
print(adder(5)(10))
# asmpython (beta/3.14.0) rejects at compile: [E001] undefined variable 'n'
