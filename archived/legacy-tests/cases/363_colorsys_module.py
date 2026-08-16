# expect:
# 0.0
# 1.0
# 1.0

import colorsys

hsv: list[float] = colorsys.rgb_to_hsv(1.0, 0.0, 0.0)
print(hsv[0])
print(hsv[1])
print(hsv[2])
