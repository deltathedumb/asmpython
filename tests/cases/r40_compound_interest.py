# expect:
# 1157.62
principal = 1000
rate = 0.05
years = 3
amount = principal
for _ in range(years):
    amount = amount + amount * rate
print(round(amount, 2))
# asmpython (beta/3.14.0) MISMATCH: prints '5.131006077194019e+18\n' (wrong).
