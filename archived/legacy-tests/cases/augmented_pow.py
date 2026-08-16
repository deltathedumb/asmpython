# expect:
# 32
x = 2
x **= 5
print(x)
# asmpython (beta/3.14.0) rejects at compile: unsupported augassign op '**'
