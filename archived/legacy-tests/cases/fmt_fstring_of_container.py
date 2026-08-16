# probes: an f-string renders a container with repr
# expect:
# [1, 'two']
# {'k': 1}
xs = [1, "two"]
d = {"k": 1}
print(f"{xs}")
print(f"{d}")
