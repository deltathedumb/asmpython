/* <threads.h> -- refused, and here is why.

   There is exactly one thread. The platform floor is `plat_write`,
   `plat_exit` and `plat_heap`; none of them can create one, and a
   `thrd_create` that returned an error would leave a program running its
   work nowhere. Refusing at the include is the honest failure.

   `<stdatomic.h>` IS supported, and its being supported is not a
   contradiction: with one thread every operation is already atomic, so those
   are ordinary operations with the right names. */
#error <threads.h> is not supported: the platform floor has no way to create a thread.
