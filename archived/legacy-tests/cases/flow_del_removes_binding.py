# probes: del unbinds a local name
# expect:
# here
# unbound
value = "here"
print(value)
del value
try:
    print(value)
except NameError:
    print("unbound")
