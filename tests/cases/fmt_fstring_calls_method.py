# probes: an f-string may call a method
# expect:
# ADA
name = "ada"
print(f"{name.upper()}")
