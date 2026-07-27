package asmpython.jvm;

import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * Calling ordinary Java from compiled Python.
 *
 * <p>Compiled code holds no JVM references — every value it has is a 64-bit
 * word (see {@link Memory}) — so a Java object cannot be a value. It is a
 * <em>handle</em>: an index into a table kept here, which the Python side
 * passes around as an opaque integer and hands back when it wants something
 * done. That is the same shape JNI and ctypes use, and it is what lets the
 * value model stay "everything is a long".
 *
 * <p>Handles start above the heap so mistaking one for an address is a lookup
 * miss rather than a silent read of unrelated memory.
 *
 * <h2>Marshalling</h2>
 * Arguments cross as words plus a <em>kind</em> saying how to read each one,
 * because a word alone cannot say whether it is the number 5 or the address of
 * a string. The caller knows; the callee cannot guess.
 *
 * <h2>Overloads</h2>
 * Resolution is by name and arity, then by whether the marshalled arguments
 * fit. That is less precise than javac and deliberately so: the Python side
 * has no type annotations to dispatch on. Where two overloads of the same
 * arity both fit, the first declared wins, and the honest fix is
 * {@link #callExact} naming the descriptor.
 */
public class Java extends Containers {

    /** Above the heap, so a handle can never be read as an address. */
    private static final long HANDLE_BASE = 1L << 40;

    private static final List<Object> OBJECTS = new ArrayList<>();

    // ---- argument kinds, as the lowering emits them ----------------------

    public static final long KIND_INT = 0;
    public static final long KIND_STRING = 1;
    public static final long KIND_DOUBLE = 2;
    public static final long KIND_BOOL = 3;
    public static final long KIND_HANDLE = 4;
    public static final long KIND_NULL = 5;

    protected Java() {
    }

    // ---- handles ---------------------------------------------------------

    public static synchronized long handle(Object value) {
        if (value == null) {
            return 0;
        }
        OBJECTS.add(value);
        return HANDLE_BASE + OBJECTS.size() - 1;
    }

    public static synchronized Object target(long handle) {
        if (handle == 0) {
            return null;
        }
        if (handle < HANDLE_BASE) {
            // A heap address, not a handle: a Python string being used as a
            // receiver. A Java method returning String hands back a Python
            // string rather than a handle -- that is what callers want almost
            // always -- so the one thing left to make work is calling back
            // into java.lang.String on it.
            return readString(handle);
        }
        int index = (int) (handle - HANDLE_BASE);
        if (index < 0 || index >= OBJECTS.size()) {
            throw new IllegalStateException(
                    "asmpython: " + handle + " is not a Java handle");
        }
        return OBJECTS.get(index);
    }

    // ---- lookup ----------------------------------------------------------

    /** {@code jvm_class(name)} — a handle to a Class, by binary name. */
    public static long jvm_class(long name) {
        String binary = readString(name);
        try {
            return handle(Class.forName(binary, false, classLoader()));
        } catch (ClassNotFoundException e) {
            _abi_raise(allocateString(
                    "ImportError: no Java class named '" + binary + "'"), 0);
            return 0;
        }
    }

    /**
     * The loader to resolve against.
     *
     * <p>This class's own loader, not the system one: in a plugin host the
     * interesting classes are the host's, and the system loader cannot see
     * them.
     */
    private static ClassLoader classLoader() {
        ClassLoader loader = Java.class.getClassLoader();
        return loader != null ? loader : ClassLoader.getSystemClassLoader();
    }

    // ---- construction and calls -----------------------------------------

    /**
     * {@code jvm_new(classHandle, argWords, argKinds, count)} — construct.
     *
     * <p>Arrays rather than varargs so one entry point covers every arity;
     * the codegen builds them the same way it builds printf's.
     */
    public static long jvm_new(long classHandle, long words, long kinds, long count) {
        Class<?> type = asClass(classHandle);
        Object[] arguments = marshal(words, kinds, count);
        for (Constructor<?> candidate : type.getConstructors()) {
            if (candidate.getParameterCount() != arguments.length) {
                continue;
            }
            try {
                return handle(candidate.newInstance(coerce(arguments,
                        candidate.getParameterTypes())));
            } catch (IllegalArgumentException e) {
                continue;                       // wrong overload; try the next
            } catch (ReflectiveOperationException e) {
                throw reported(type.getName() + "()", e);
            }
        }
        _abi_raise(allocateString("TypeError: no constructor of "
                + type.getName() + " takes " + arguments.length + " argument(s)"), 0);
        return 0;
    }

    /** {@code jvm_call(target, name, argWords, argKinds, count)} — instance or static. */
    public static long jvm_call(long target, long name, long words, long kinds, long count) {
        Object receiver = target(target);
        String method = readString(name);
        Object[] arguments = marshal(words, kinds, count);

        boolean isStatic = receiver instanceof Class<?>;
        Class<?> type = isStatic ? (Class<?>) receiver : receiver.getClass();

        for (Method candidate : type.getMethods()) {
            if (!candidate.getName().equals(method)
                    || candidate.getParameterCount() != arguments.length) {
                continue;
            }
            try {
                Object result = candidate.invoke(isStatic ? null : receiver,
                        coerce(arguments, candidate.getParameterTypes()));
                return wrap(result, candidate.getReturnType());
            } catch (IllegalArgumentException e) {
                continue;
            } catch (ReflectiveOperationException e) {
                throw reported(type.getName() + "." + method, e);
            }
        }
        _abi_raise(allocateString("AttributeError: " + type.getName() + " has no method '"
                + method + "' taking " + arguments.length + " argument(s)"), 0);
        return 0;
    }

    /** As {@link #jvm_call}, but pinned to one descriptor when overloads clash. */
    public static long callExact(long target, long name, long descriptor,
                                 long words, long kinds, long count) {
        Object receiver = target(target);
        String method = readString(name);
        String wanted = readString(descriptor);
        boolean isStatic = receiver instanceof Class<?>;
        Class<?> type = isStatic ? (Class<?>) receiver : receiver.getClass();

        for (Method candidate : type.getMethods()) {
            if (!candidate.getName().equals(method) || !descriptorOf(candidate).equals(wanted)) {
                continue;
            }
            try {
                Object result = candidate.invoke(isStatic ? null : receiver,
                        coerce(marshal(words, kinds, count), candidate.getParameterTypes()));
                return wrap(result, candidate.getReturnType());
            } catch (ReflectiveOperationException e) {
                throw reported(type.getName() + "." + method, e);
            }
        }
        _abi_raise(allocateString("AttributeError: no " + type.getName() + "."
                + method + wanted), 0);
        return 0;
    }

    /** {@code jvm_field(target, name)} — read a field, instance or static. */
    public static long jvm_field(long target, long name) {
        Object receiver = target(target);
        String field = readString(name);
        boolean isStatic = receiver instanceof Class<?>;
        Class<?> type = isStatic ? (Class<?>) receiver : receiver.getClass();
        try {
            Field found = type.getField(field);
            return wrap(found.get(isStatic ? null : receiver), found.getType());
        } catch (NoSuchFieldException e) {
            _abi_raise(allocateString("AttributeError: " + type.getName()
                    + " has no field '" + field + "'"), 0);
            return 0;
        } catch (ReflectiveOperationException e) {
            throw reported(type.getName() + "." + field, e);
        }
    }

    /** {@code jvm_str(handle)} — toString, as a heap string. */
    public static long jvm_str(long target) {
        Object value = target(target);
        return allocateString(String.valueOf(value));
    }

    // ---- marshalling -----------------------------------------------------

    private static Class<?> asClass(long classHandle) {
        Object value = target(classHandle);
        if (!(value instanceof Class<?>)) {
            _abi_raise(allocateString("TypeError: " + value + " is not a Java class"), 0);
        }
        return (Class<?>) value;
    }

    private static Object[] marshal(long words, long kinds, long count) {
        Object[] out = new Object[(int) count];
        for (int i = 0; i < out.length; i++) {
            long word = loadLong(words + (long) i * 8);
            long kind = loadLong(kinds + (long) i * 8);
            if (kind == KIND_STRING) {
                out[i] = readString(word);
            } else if (kind == KIND_DOUBLE) {
                out[i] = Double.longBitsToDouble(word);
            } else if (kind == KIND_BOOL) {
                out[i] = word != 0;
            } else if (kind == KIND_HANDLE) {
                out[i] = target(word);
            } else if (kind == KIND_NULL) {
                out[i] = null;
            } else {
                out[i] = word;
            }
        }
        return out;
    }

    /**
     * Narrow marshalled arguments to a method's declared parameter types.
     *
     * <p>Python has one integer type and Java has six, so a `long` has to
     * become whatever the method actually wants or every call to an `int`
     * parameter fails to match.
     */
    private static Object[] coerce(Object[] arguments, Class<?>[] types) {
        Object[] out = new Object[arguments.length];
        for (int i = 0; i < arguments.length; i++) {
            out[i] = coerceOne(arguments[i], types[i]);
        }
        return out;
    }

    private static Object coerceOne(Object value, Class<?> type) {
        if (value instanceof Long) {
            long raw = (Long) value;
            if (type == int.class || type == Integer.class) {
                return (int) raw;
            }
            if (type == short.class || type == Short.class) {
                return (short) raw;
            }
            if (type == byte.class || type == Byte.class) {
                return (byte) raw;
            }
            if (type == char.class || type == Character.class) {
                return (char) raw;
            }
            if (type == float.class || type == Float.class) {
                return (float) raw;
            }
            if (type == double.class || type == Double.class) {
                return (double) raw;
            }
            if (type == boolean.class || type == Boolean.class) {
                return raw != 0;
            }
        } else if (value instanceof Double) {
            double raw = (Double) value;
            if (type == float.class || type == Float.class) {
                return (float) raw;
            }
            if (type == int.class || type == Integer.class) {
                return (int) raw;
            }
            if (type == long.class || type == Long.class) {
                return (long) raw;
            }
        }
        return value;
    }

    /**
     * A returned Java value as one word.
     *
     * <p>Strings come back as heap addresses and numbers as themselves, so the
     * Python side reads the word according to what it expects — the same
     * convention as every other host call in this runtime. Everything else
     * gets a handle rather than being stringified into something unusable.
     */
    private static long wrap(Object result, Class<?> declared) {
        if (declared == void.class || result == null) {
            return 0;
        }
        if (result instanceof String) {
            return allocateString((String) result);
        }
        if (result instanceof Boolean) {
            return ((Boolean) result) ? 1 : 0;
        }
        if (result instanceof Character) {
            return allocateString(result.toString());
        }
        if (result instanceof Double || result instanceof Float) {
            return Double.doubleToRawLongBits(((Number) result).doubleValue());
        }
        if (result instanceof Number) {
            return ((Number) result).longValue();
        }
        return handle(result);
    }

    private static String descriptorOf(Method method) {
        StringBuilder out = new StringBuilder("(");
        for (Class<?> parameter : method.getParameterTypes()) {
            out.append(typeDescriptor(parameter));
        }
        return out.append(")").append(typeDescriptor(method.getReturnType())).toString();
    }

    private static String typeDescriptor(Class<?> type) {
        if (type == void.class) {
            return "V";
        }
        if (type == boolean.class) {
            return "Z";
        }
        if (type == byte.class) {
            return "B";
        }
        if (type == char.class) {
            return "C";
        }
        if (type == short.class) {
            return "S";
        }
        if (type == int.class) {
            return "I";
        }
        if (type == long.class) {
            return "J";
        }
        if (type == float.class) {
            return "F";
        }
        if (type == double.class) {
            return "D";
        }
        if (type.isArray()) {
            return "[" + typeDescriptor(type.getComponentType());
        }
        return "L" + type.getName().replace('.', '/') + ";";
    }

    /**
     * Report the real cause of a reflective failure.
     *
     * <p>An InvocationTargetException's own message is always null, so leaving
     * it wrapped turns "the Java method threw" into no information at all.
     */
    private static RuntimeException reported(String what, ReflectiveOperationException e) {
        Throwable cause = e.getCause() == null ? e : e.getCause();
        return new AsmPythonError(what + " threw " + cause, 0);
    }

    // ======================================================================
    // Typed entry points
    // ======================================================================
    //
    // asmpython's FFI declares a fixed arity and a type per argument, so the
    // binding module cannot describe a variadic call. These name their shape
    // instead -- `_s` takes a string, `_i` an int, `_o` a handle -- which is
    // the same thing JNI does with CallIntMethod/CallObjectMethod and for the
    // same reason: the word alone cannot say how to read itself.
    //
    // The `s`-prefixed variants return a STRING (a heap address); the plain
    // ones return a word. Which to use is a property of the Java method being
    // called, and the Python side has no annotation to infer it from.

    private static long invoke(long target, String method, Object[] arguments,
                               boolean wantString) {
        Object receiver = target(target);
        boolean isStatic = receiver instanceof Class<?>;
        Class<?> type = isStatic ? (Class<?>) receiver : receiver.getClass();

        Method best = pick(type, method, arguments);
        if (best == null && isStatic) {
            // A Class handle means "call a static method of this class" almost
            // always, but a Class is also an ordinary object with methods of
            // its own. Without this fallback `getClass().getName()` cannot be
            // expressed at all: getName is an instance method OF Class, and
            // looking only for statics on the class it describes never finds
            // it.
            best = pick(Class.class, method, arguments);
            if (best != null) {
                isStatic = false;
            }
        }
        if (best == null) {
            _abi_raise(allocateString("AttributeError: " + type.getName()
                    + " has no method '" + method + "' taking "
                    + arguments.length + " argument(s)"), 0);
            return 0;
        }
        try {
            Object result = best.invoke(isStatic ? null : receiver,
                    coerce(arguments, best.getParameterTypes()));
            if (wantString) {
                return result == null ? allocateString("null")
                                      : allocateString(String.valueOf(result));
            }
            return wrap(result, best.getReturnType());
        } catch (ReflectiveOperationException e) {
            throw reported(type.getName() + "." + method, e);
        }
    }

    /**
     * The best-fitting overload, or null.
     *
     * <p>Scored rather than first-match, because `getMethods()` has no defined
     * order and Python has one integer type where Java has six. First-match
     * makes `Math.max(17, 42)` resolve to `max(double, double)` about half the
     * time and return 42.0 as a bit pattern -- a wrong answer with no error,
     * which is the worst way to be wrong.
     */
    private static Method pick(Class<?> type, String name, Object[] arguments) {
        Method best = null;
        int bestScore = Integer.MIN_VALUE;
        for (Method candidate : type.getMethods()) {
            if (!candidate.getName().equals(name)
                    || candidate.getParameterCount() != arguments.length) {
                continue;
            }
            int score = fit(arguments, candidate.getParameterTypes());
            if (score > bestScore) {
                bestScore = score;
                best = candidate;
            }
        }
        return bestScore == REJECT ? null : best;
    }

    private static final int REJECT = Integer.MIN_VALUE;

    private static int fit(Object[] arguments, Class<?>[] types) {
        int total = 0;
        for (int i = 0; i < arguments.length; i++) {
            int score = fitOne(arguments[i], types[i]);
            if (score == REJECT) {
                return REJECT;
            }
            total += score;
        }
        return total;
    }

    /** 2 = the type Python actually has, 1 = a lossless-enough conversion. */
    private static int fitOne(Object value, Class<?> type) {
        if (value == null) {
            return type.isPrimitive() ? REJECT : 1;
        }
        if (value instanceof Long) {
            if (type == long.class || type == Long.class) {
                return 2;
            }
            if (type == int.class || type == Integer.class) {
                return 1;
            }
            if (type == short.class || type == byte.class || type == char.class
                    || type == float.class || type == double.class
                    || type == Short.class || type == Byte.class
                    || type == Character.class || type == Float.class
                    || type == Double.class) {
                return 0;
            }
            return type.isAssignableFrom(Long.class) ? 0 : REJECT;
        }
        if (value instanceof Double) {
            if (type == double.class || type == Double.class) {
                return 2;
            }
            if (type == float.class || type == Float.class) {
                return 1;
            }
            if (type.isPrimitive()) {
                return 0;
            }
            return type.isAssignableFrom(Double.class) ? 0 : REJECT;
        }
        if (value instanceof Boolean) {
            return (type == boolean.class || type == Boolean.class) ? 2
                    : (type.isAssignableFrom(Boolean.class) ? 0 : REJECT);
        }
        if (type.isInstance(value)) {
            return type == value.getClass() ? 2 : 1;
        }
        return REJECT;
    }

    private static final Object[] NONE = new Object[0];

    public static long jcall(long target, long name) {
        return invoke(target, readString(name), NONE, false);
    }

    public static long jcalls(long target, long name) {
        return invoke(target, readString(name), NONE, true);
    }

    public static long jcall_s(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{readString(a)}, false);
    }

    public static long jcalls_s(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{readString(a)}, true);
    }

    public static long jcall_i(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{a}, false);
    }

    public static long jcalls_i(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{a}, true);
    }

    public static long jcall_o(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{target(a)}, false);
    }

    public static long jcalls_o(long target, long name, long a) {
        return invoke(target, readString(name), new Object[]{target(a)}, true);
    }

    public static long jcall_ss(long target, long name, long a, long b) {
        return invoke(target, readString(name),
                new Object[]{readString(a), readString(b)}, false);
    }

    public static long jcall_si(long target, long name, long a, long b) {
        return invoke(target, readString(name), new Object[]{readString(a), b}, false);
    }

    public static long jcall_so(long target, long name, long a, long b) {
        return invoke(target, readString(name),
                new Object[]{readString(a), target(b)}, false);
    }

    public static long jcall_oo(long target, long name, long a, long b) {
        return invoke(target, readString(name),
                new Object[]{target(a), target(b)}, false);
    }

    public static long jcall_ii(long target, long name, long a, long b) {
        return invoke(target, readString(name), new Object[]{a, b}, false);
    }

    public static long jcall_io(long target, long name, long a, long b) {
        return invoke(target, readString(name), new Object[]{a, target(b)}, false);
    }

    // ---- construction, same shapes --------------------------------------

    private static long construct(long classHandle, Object[] arguments) {
        Class<?> type = asClass(classHandle);
        for (Constructor<?> candidate : type.getConstructors()) {
            if (candidate.getParameterCount() != arguments.length) {
                continue;
            }
            try {
                return handle(candidate.newInstance(
                        coerce(arguments, candidate.getParameterTypes())));
            } catch (IllegalArgumentException e) {
                continue;
            } catch (ReflectiveOperationException e) {
                throw reported(type.getName() + "()", e);
            }
        }
        _abi_raise(allocateString("TypeError: no constructor of " + type.getName()
                + " takes " + arguments.length + " argument(s)"), 0);
        return 0;
    }

    public static long jnew(long classHandle) {
        return construct(classHandle, NONE);
    }

    public static long jnew_s(long classHandle, long a) {
        return construct(classHandle, new Object[]{readString(a)});
    }

    public static long jnew_i(long classHandle, long a) {
        return construct(classHandle, new Object[]{a});
    }

    public static long jnew_o(long classHandle, long a) {
        return construct(classHandle, new Object[]{target(a)});
    }

    // ---- fields, as words or strings -------------------------------------

    public static long jfield(long target, long name) {
        return jvm_field(target, name);
    }

    public static long jfields(long target, long name) {
        Object receiver = target(target);
        boolean isStatic = receiver instanceof Class<?>;
        Class<?> type = isStatic ? (Class<?>) receiver : receiver.getClass();
        try {
            Object value = type.getField(readString(name)).get(isStatic ? null : receiver);
            return allocateString(String.valueOf(value));
        } catch (NoSuchFieldException e) {
            _abi_raise(allocateString("AttributeError: " + type.getName()
                    + " has no field '" + readString(name) + "'"), 0);
            return 0;
        } catch (ReflectiveOperationException e) {
            throw reported(type.getName() + "." + readString(name), e);
        }
    }

    /** {@code jclass(name)} — the name the `java` module binds to. */
    public static long jclass(long name) {
        return jvm_class(name);
    }

    // ---- named entry points, for `import java.<package> as p` ------------
    //
    // These take a real java.lang.String rather than a heap address, because
    // the class name comes from the IMPORT rather than from a Python value:
    // the codegen has it at compile time and can push it with `ldc`, which
    // saves interning a copy in the heap on every call.

    /**
     * A class named by an import, resolved as written or under `java.`.
     *
     * <p>`import java.util as u` gives `util.ArrayList`, and `import
     * java.net.neoforged.neoforge as n` gives `net.neoforged.neoforge.X`. Both
     * are legitimate and no compile-time rule separates them -- `java.net` and
     * `net.neoforged` both exist -- so the resolution is simply attempted.
     */
    public static long jclass_named(String className) {
        Class<?> found = tryLoad(className);
        if (found == null) {
            found = tryLoad("java." + className);
        }
        if (found == null) {
            _abi_raise(allocateString("ImportError: no Java class named '"
                    + className + "' or 'java." + className + "'"), 0);
            return 0;
        }
        return handle(found);
    }

    private static Class<?> tryLoad(String name) {
        try {
            return Class.forName(name, false, classLoader());
        } catch (ClassNotFoundException | NoClassDefFoundError e) {
            return null;
        }
    }

    public static long jnew_named(String className) {
        return construct(jclass_named(className), NONE);
    }

    public static long jnew_named_s(String className, long a) {
        return construct(jclass_named(className), new Object[]{readString(a)});
    }

    public static long jnew_named_i(String className, long a) {
        return construct(jclass_named(className), new Object[]{a});
    }

    /** {@code jnull()} — the handle for Java null, so it can be passed along. */
    public static long jnull() {
        return 0;
    }

    /** {@code jstr(handle)} — String.valueOf, for printing a Java object. */
    public static long jstr(long target) {
        return jvm_str(target);
    }

    // ======================================================================
    // Python implementing a Java interface
    // ======================================================================
    //
    // Java APIs built on callbacks -- listeners, suppliers, consumers -- cannot
    // be reached at all otherwise. Compiled code has no way to hand Java a
    // function, so this goes the other way: a java.lang.reflect.Proxy stands in
    // for the interface and forwards every call to a static method on the
    // generated program class.
    //
    // That is what turns "call Java from Python" into "take part in a Java
    // framework". Registering a Minecraft item, to pick the case this was built
    // for, is a Supplier handed to a DeferredRegister -- there is no
    // callback-free version of it.

    private static Class<?> programClass;

    /**
     * Tell the runtime which class holds the compiled Python.
     *
     * <p>Emitted into `<clinit>` by the backend, because only the compiler
     * knows the generated class's name and only the runtime needs it.
     */
    public static void installProgram(String className) {
        programClass = tryLoad(className);
    }

    /**
     * {@code jproxy(interfaceName, callback)} — an object implementing
     * `interfaceName` whose methods call the Python function `callback`.
     *
     * <p>The Python side must be exported (`@access(Public)`) or reachability
     * analysis drops it and the proxy has nothing to call.
     *
     * <p>Arguments arrive as words: a handle for an object, an address for a
     * string. The return word is read back according to what the interface
     * method declares, which is the one place the Java side knows more than
     * the Python side and can say so.
     */
    public static long jproxy(long interfaceName, long callback) {
        String name = readString(interfaceName);
        String method = readString(callback);

        Class<?> type = tryLoad(name);
        if (type == null) {
            type = tryLoad("java." + name);
        }
        if (type == null || !type.isInterface()) {
            _abi_raise(allocateString("TypeError: " + name
                    + " is not an interface that can be implemented"), 0);
            return 0;
        }
        if (programClass == null) {
            _abi_raise(allocateString(
                    "RuntimeError: jproxy needs the program class; "
                    + "the backend should have installed it"), 0);
            return 0;
        }

        Object proxy = java.lang.reflect.Proxy.newProxyInstance(
                type.getClassLoader() != null ? type.getClassLoader() : classLoader(),
                new Class<?>[]{type},
                (self, called, arguments) -> dispatch(method, called, arguments));
        return handle(proxy);
    }

    private static Object dispatch(String callback, Method called, Object[] arguments)
            throws Throwable {
        Object[] safe = arguments == null ? NONE : arguments;

        // Object's own methods must not be forwarded: a proxy is still an
        // object, and sending toString/equals/hashCode to Python would break
        // anything that puts it in a collection or logs it.
        if (called.getDeclaringClass() == Object.class) {
            switch (called.getName()) {
                case "toString":
                    return "<python " + callback + ">";
                case "hashCode":
                    return System.identityHashCode(safe.length == 0 ? callback : safe);
                case "equals":
                    return safe.length == 1 && safe[0] != null
                            && System.identityHashCode(safe[0]) == 0;
                default:
                    break;
            }
        }

        Class<?>[] wordTypes = new Class<?>[safe.length];
        java.util.Arrays.fill(wordTypes, long.class);
        Method target;
        try {
            target = programClass.getMethod(callback, wordTypes);
        } catch (NoSuchMethodException e) {
            throw new AsmPythonError("jproxy: " + programClass.getName() + " has no "
                    + callback + " taking " + safe.length
                    + " argument(s) -- is it marked @access(Public)?", 0);
        }

        Object[] words = new Object[safe.length];
        for (int i = 0; i < safe.length; i++) {
            words[i] = toWord(safe[i]);
        }
        Object result = target.invoke(null, words);
        long word = result instanceof Long ? (Long) result : 0L;
        return fromWord(unbox(word), called.getReturnType());
    }

    /** A Java value as the word compiled Python will see. */
    private static long toWord(Object value) {
        if (value == null) {
            return 0;
        }
        if (value instanceof String) {
            return allocateString((String) value);
        }
        if (value instanceof Boolean) {
            return ((Boolean) value) ? 1 : 0;
        }
        if (value instanceof Double || value instanceof Float) {
            return Double.doubleToRawLongBits(((Number) value).doubleValue());
        }
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        return handle(value);
    }

    /**
     * The payload of a boxed scalar, or the word unchanged.
     *
     * <p>An EXPORTED Python function returns a BOX -- a heap cell holding a
     * tag and a payload -- because its return type is "any" and the lowering
     * cannot know better. A Python caller unboxes it as a matter of course; a
     * Java caller reaching in through reflection just gets the cell, and would
     * otherwise see every returned object as a meaningless address.
     */
    private static long unbox(long word) {
        return isBox(word) != 0 ? loadLong(word + BOX_PAYLOAD_OFF) : word;
    }

    /** A word from Python as the type the interface method declares. */
    private static Object fromWord(long word, Class<?> declared) {
        if (declared == void.class) {
            return null;
        }
        if (declared == boolean.class || declared == Boolean.class) {
            return word != 0;
        }
        if (declared == int.class || declared == Integer.class) {
            return (int) word;
        }
        if (declared == long.class || declared == Long.class) {
            return word;
        }
        if (declared == double.class || declared == Double.class) {
            return Double.longBitsToDouble(word);
        }
        if (declared == float.class || declared == Float.class) {
            return (float) Double.longBitsToDouble(word);
        }
        if (declared == String.class || declared == CharSequence.class) {
            return word == 0 ? null : readString(word);
        }
        if (word == 0) {
            return null;
        }
        // An object-typed return. A handle is unambiguous; anything smaller is
        // a number, because a word carries no tag and the two cases cannot be
        // told apart otherwise. That makes `Supplier<Item>` work -- Python
        // returns a handle -- while a Python string returned through an
        // Object-typed callback arrives as its address rather than its text.
        // Declare the interface method as returning String to get the string.
        return isHandle(word) ? target(word) : Long.valueOf(word);
    }

    private static synchronized boolean isHandle(long word) {
        long index = word - HANDLE_BASE;
        return index >= 0 && index < OBJECTS.size();
    }

    // ---- arrays ----------------------------------------------------------

    public static long jvm_array(long classHandle, long length) {
        return handle(Array.newInstance(asClass(classHandle), (int) length));
    }

    public static long jvm_array_get(long arrayHandle, long index) {
        Object array = target(arrayHandle);
        Object value = Array.get(array, (int) index);
        return wrap(value, array.getClass().getComponentType());
    }

    public static long jvm_array_length(long arrayHandle) {
        return Array.getLength(target(arrayHandle));
    }
}
