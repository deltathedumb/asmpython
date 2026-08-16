# probes: %Nd pads to a width
# expect:
# [   42]
# [42   ]
# [00042]
print("[%5d]" % 42)
print("[%-5d]" % 42)
print("[%05d]" % 42)
