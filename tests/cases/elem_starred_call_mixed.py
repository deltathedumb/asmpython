# probes: f(*xs) spreads elements into parameters (mixed elements)
# expect:
# [1, 'two', 3.5]
def take3(a, b, c):
    return [a, b, c]


xs = [1, "two", 3.5]
print(take3(*xs))
