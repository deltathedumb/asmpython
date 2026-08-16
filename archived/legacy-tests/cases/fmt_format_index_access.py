# probes: a field may subscript a sequence
# expect:
# b
print("{0[1]}".format(["a", "b"]))
