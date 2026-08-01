# tier: spec
# ref: reference/lexical_analysis.html#f-strings
# expect:
#     3.14|
#      3.14159|
# 1
# 2
# nested 2
# v=3.14
w = 8
v = 3.14159
print(f"{v:{w}.2f}" + "|")
print(f"{v!r:>12}" + "|")
print(f"{ {'k': 1}['k'] }")
print(f"{[1, 2][1]}")
print(f"{'nested ' + f'{1 + 1}'}")
print(f"{v=:.2f}")
