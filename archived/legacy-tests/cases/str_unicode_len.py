# expect:
# 5
print(len('héllo'))
# len() of a non-ASCII str counts bytes not code points; asmpython prints 6, not 5.
