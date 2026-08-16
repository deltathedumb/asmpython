# probes: f(*xs) spreads elements into parameters (float elements)
# expect:
# [1.5, 2.5, 3.5]
def take3(a, b, c):
    return [a, b, c]


xs = [1.5, 2.5, 3.5]
print(take3(*xs))
