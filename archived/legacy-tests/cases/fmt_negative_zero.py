# probes: negative zero keeps its sign
# expect:
# -0.0
# -0.0
# -0.0
print(-0.0)
print(f"{-0.0}")
print(format(-0.0, ".1f"))
