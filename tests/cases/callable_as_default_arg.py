# expect:
# 5 50
def process(x, transform=lambda v: v):
    return transform(x)
print(process(5), process(5, lambda v: v * 10))
# asmpython (beta/3.14.0) rejects at compile: [P001] unexpected token KEYWORD 'lambda'
