# probes: dict.items yields key/value pairs
# expect:
# a 1
# b 2
d = {"a": 1, "b": 2}
for key, value in d.items():
    print(key, value)
