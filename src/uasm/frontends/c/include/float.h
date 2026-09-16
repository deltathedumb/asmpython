/* <float.h>.

   `long double` IS 80-BIT EXTENDED, which is x86-64's, so the LDBL_* macros
   below are that format's: 64 bits of significand, an exponent range far
   wider than double's, and a size of 16 with 10 bytes in use. The arithmetic
   is software (`support.py`'s `ldouble` unit) and the format is the ABI's, so
   a program that prints LDBL_MAX prints what a hosted compiler would.

   FLT_EVAL_METHOD IS 0: an operation is evaluated in its own type and not in
   a wider one. The x87 hardware would say 2 (everything in long double) and
   this does not use the x87 hardware.
*/
#ifndef _UASM_FLOAT_H
#define _UASM_FLOAT_H

#define FLT_RADIX 2
#define FLT_ROUNDS 1
#define FLT_EVAL_METHOD 0
#define DECIMAL_DIG 17

#define FLT_MANT_DIG 24
#define FLT_DIG 6
#define FLT_MIN_EXP (-125)
#define FLT_MIN_10_EXP (-37)
#define FLT_MAX_EXP 128
#define FLT_MAX_10_EXP 38
#define FLT_MAX 3.40282346638528859812e+38F
#define FLT_MIN 1.17549435082228750797e-38F
#define FLT_EPSILON 1.19209289550781250000e-7F
#define FLT_TRUE_MIN 1.40129846432481707092e-45F
#define FLT_DECIMAL_DIG 9

#define DBL_MANT_DIG 53
#define DBL_DIG 15
#define DBL_MIN_EXP (-1021)
#define DBL_MIN_10_EXP (-307)
#define DBL_MAX_EXP 1024
#define DBL_MAX_10_EXP 308
#define DBL_MAX 1.79769313486231570815e+308
#define DBL_MIN 2.22507385850720138309e-308
#define DBL_EPSILON 2.22044604925031308085e-16
#define DBL_TRUE_MIN 4.94065645841246544177e-324
#define DBL_DECIMAL_DIG 17

#define LDBL_MANT_DIG 64
#define LDBL_DIG 18
#define LDBL_MIN_EXP (-16381)
#define LDBL_MIN_10_EXP (-4931)
#define LDBL_MAX_EXP 16384
#define LDBL_MAX_10_EXP 4932
#define LDBL_MAX 1.18973149535723176502e+4932L
#define LDBL_MIN 3.36210314311209350626e-4932L
#define LDBL_EPSILON 1.08420217248550443401e-19L
#define LDBL_TRUE_MIN 3.64519953188247460253e-4951L
#define LDBL_DECIMAL_DIG 21

#endif
