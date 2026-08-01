# tier: spec
# ref: reference/datamodel.html#traceback-objects
# min-python: 3.11
# expect:
# True
# True
# True
# True
try:
    (1).missing
except AttributeError as e:
    tb = e.__traceback__
    print(tb is not None)
    print(hasattr(tb.tb_frame.f_code, "co_positions"))
    positions = list(tb.tb_frame.f_code.co_positions())
    print(len(positions) > 0)
    print(all(len(p) == 4 for p in positions))
