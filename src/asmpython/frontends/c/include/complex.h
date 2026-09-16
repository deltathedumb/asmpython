/* <complex.h> -- refused, and here is why.

   `_Complex` is a pair of floating values that arithmetic treats as ONE, and
   the APIR's type system is thirteen machine storage classes with nothing
   aggregate among them. Every operator would have to be lowered to a pair of
   calls over a two-field struct, `<tgmath.h>` would have to dispatch on a
   type that does not exist, and `sizeof(double _Complex)` would be a lie the
   moment anything passed one by value.

   A program that needs complex arithmetic can carry its own `struct { double
   re, im; }` and the four operators, which is what this header would compile
   to anyway -- but written where the reader can see it. */
#error <complex.h> is not supported: the APIR has no complex type, and every operator on one would be a call. Use a struct of two doubles.

typedef struct { double __re, __im; } __c_complex_placeholder;
