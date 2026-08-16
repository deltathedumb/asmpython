# expect:
# trying...
# got: bad input
# done
print("trying...")
try:
    raise "bad input"
except as msg:
    print("got:", msg)
print("done")
