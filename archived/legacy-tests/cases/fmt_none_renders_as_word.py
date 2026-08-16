# probes: None formats as None everywhere
# expect:
# None
# None
# None
# None
n = None
print(f"{n}")
print("{}".format(n))
print("%s" % (n,))
print(str(n))
