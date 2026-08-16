# expect:
# caught: boom
# done
def explode():
    raise "boom"

try:
    explode()
except ValueError as e:
    print("caught: " + e)
print("done")
