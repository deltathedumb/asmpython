# probes: %% emits a literal percent sign
# expect:
# 100% done
# 50%
print("100%% done" % ())
print("%d%%" % 50)
