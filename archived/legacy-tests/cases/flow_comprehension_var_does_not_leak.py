# probes: a comprehension's loop name stays inside it
# expect:
# [1, 4, 9]
# outer
n = "outer"
squares = [n * n for n in [1, 2, 3]]
print(squares)
print(n)
