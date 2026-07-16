# expect:
# 1
# 2

extend constants
const X = 1
retract constants
y = 2
print(X)
print(y)
