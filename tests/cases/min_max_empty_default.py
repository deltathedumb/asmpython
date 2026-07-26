# expect:
# 5
print(max([5], default=0))
# asmpython (beta/3.14.0) rejects at compile: [E021] unexpected keyword argument 'default'
