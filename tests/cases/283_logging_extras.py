# expect:
# done

from logging import captureWarnings, shutdown

captureWarnings(1)
shutdown()
print("done")
