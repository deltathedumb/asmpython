# probe78: string formatting - format spec, alignment, padding
# %d formatting
print("%d" % 42)          # 42
print("%05d" % 42)        # 00042
print("%-5d|" % 42)      # 42   |
print("%+d" % 42)         # +42
print("%+d" % -42)        # -42
print("%x" % 255)         # ff
print("%X" % 255)         # FF
print("%o" % 8)           # 10

# %s formatting
print("%s" % "hello")     # hello
print("%10s" % "hi")      # hi (right-aligned 10)
print("%-10s|" % "hi")   # hi        | (left-aligned 10)

# %f formatting
# Note: may not be implemented yet
# print("%f" % 3.14)      # skip for now

# f-string with format spec
n = 42
print(f"{n:05d}")    # 00042
print(f"{n:>10}")    # 42 right-aligned 10
print(f"{n:<10}|")   # 42 left-aligned 10
print(f"{'hi':^10}|")  # hi centered 10
