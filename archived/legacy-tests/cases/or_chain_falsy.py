# expect:
# x
print(0 or '' or 'x')
# an or-chain over falsy values returns the wrong arm; asmpython prints '' (empty).
