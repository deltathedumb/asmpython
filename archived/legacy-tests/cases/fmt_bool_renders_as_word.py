# probes: a bool formats as True/False everywhere
# expect:
# True
# True
# True
# True
b = True
print(f"{b}")
print("{}".format(b))
print("%s" % b)
print(str(b))
