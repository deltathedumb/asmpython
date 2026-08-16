# probes: strings compare by value, not address
# expect:
# True
# False
# False
a = "hello"
b = "hel" + "lo"
print(a == b)
print(a != b)
print(a == "other")
