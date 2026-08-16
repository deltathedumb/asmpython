# probes: a conditional expression works inside a field
# expect:
# big
n = 5
print(f"{'big' if n > 3 else 'small'}")
