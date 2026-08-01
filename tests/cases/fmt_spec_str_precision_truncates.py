# probes: .N truncates a string
# expect:
# abc
print(format("abcdef", ".3"))
