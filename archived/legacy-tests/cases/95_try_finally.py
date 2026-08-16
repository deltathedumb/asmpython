# expect:
# body
# fin
# after
# fin2
# caught: boom
# done
def risky():
    raise "boom"

try:
    print("body")
finally:
    print("fin")
print("after")

try:
    try:
        risky()
    finally:
        print("fin2")
except Exception as e:
    print("caught: " + e)
print("done")
