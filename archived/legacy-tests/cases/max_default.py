# expect:
# 99
print(max([], default=99))
# asmpython (beta/3.14.0) rejects at compile: [E021] unexpected keyword argument 'default'
