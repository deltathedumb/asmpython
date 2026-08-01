# probes: a list cannot be a dict key
# expect:
# refused
try:
    {}[[1, 2]] = "x"
    print("accepted")
except TypeError:
    print("refused")
