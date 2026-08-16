# expect:
# [80.0, 40.0, 160.0]
def apply_discount(price, pct):
    return price * (1 - pct / 100)
prices = [100, 50, 200]
discounted = [round(apply_discount(p, 20), 2) for p in prices]
print(discounted)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
