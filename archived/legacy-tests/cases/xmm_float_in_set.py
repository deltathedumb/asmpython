# expect:
# True
s = {1.5, 2.5}
print(1.5 in s)
# asmpython (beta/3.14.0) rejects at compile: [E055] set elements of type float are not supported yet (sets are str/int-keyed in v1)
