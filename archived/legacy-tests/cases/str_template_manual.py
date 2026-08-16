# expect:
# Hello Alice, you are 30 years old
template = 'Hello {}, you are {} years old'
print(template.format('Alice', 30))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (str.format)
