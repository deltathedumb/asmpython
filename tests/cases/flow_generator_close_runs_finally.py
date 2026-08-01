# probes: close() unwinds the generator's finally
# expect:
# 1
# cleaned up
# closed
def guarded():
    try:
        yield 1
        yield 2
    finally:
        print("cleaned up")


gen = guarded()
print(next(gen))
gen.close()
print("closed")
