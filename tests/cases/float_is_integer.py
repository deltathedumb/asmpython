# expect:
# True False
print((3.0).is_integer(), (3.5).is_integer())
# asmpython (beta/3.14.0) rejects at compile: [E113] float has no method 'is_integer'
