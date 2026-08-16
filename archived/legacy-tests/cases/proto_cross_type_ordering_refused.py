# probes: ordering unrelated types raises TypeError
# expect:
# refused
# False
try:
    print(1 < "a")
except TypeError:
    print("refused")
print(1 == "a")
