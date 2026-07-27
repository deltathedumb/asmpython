package asmpython.jvm;

import java.io.PrintStream;

/**
 * Runtime support for asmpython's JVM backend: strings, formatting and output,
 * on top of {@link Java} (calling ordinary Java), {@link Containers} (lists,
 * dicts, boxes) and {@link Memory} (the flat heap that makes all of it
 * addressable).
 *
 * <p>Every method the generated code calls resolves here, because unresolved IR
 * calls lower to {@code invokestatic Runtime.<name>} — and {@code invokestatic}
 * finds inherited statics, so splitting the runtime across three classes is
 * invisible to the bytecode.
 *
 * <p>Subclassable on purpose: a host embedding this backend extends Runtime,
 * adds its own static host functions, and compiles with {@code --jvm-runtime}
 * pointing at the subclass. The generated code then links against one class and
 * gets both the memory primitives and the host API.
 */
public class Runtime extends Java {

    private static final PrintStream OUT = System.out;

    protected Runtime() {
    }

    // ======================================================================
    // strings
    // ======================================================================
    //
    // Every one of these returns a FRESH allocation. Python strings are
    // immutable, so a caller may hold the input across the call and must not
    // see it change; the native runtime allocates here too.

    public static long _abi_str_concat(long left, long right) {
        return allocateString(readString(left) + readString(right));
    }

    public static long _abi_str_concat_dup(long left, long right) {
        return _abi_str_concat(left, right);
    }

    public static long _abi_str_repeat(long text, long times) {
        StringBuilder out = new StringBuilder();
        String source = readString(text);
        for (long i = 0; i < times; i++) {
            out.append(source);
        }
        return allocateString(out.toString());
    }

    public static long _abi_str_upper(long text) {
        return allocateString(readString(text).toUpperCase());
    }

    public static long _abi_str_lower(long text) {
        return allocateString(readString(text).toLowerCase());
    }

    public static long _abi_str_strip(long text) {
        return allocateString(readString(text).trim());
    }

    public static long _abi_str_lstrip(long text) {
        String s = readString(text);
        int i = 0;
        while (i < s.length() && Character.isWhitespace(s.charAt(i))) {
            i++;
        }
        return allocateString(s.substring(i));
    }

    public static long _abi_str_rstrip(long text) {
        String s = readString(text);
        int end = s.length();
        while (end > 0 && Character.isWhitespace(s.charAt(end - 1))) {
            end--;
        }
        return allocateString(s.substring(0, end));
    }

    /**
     * {@code _abi_str_char_at(str, index)} — a one-character STRING, not a code
     * point. Python has no char type: {@code s[0]} is a string of length one,
     * and returning a number here would make {@code s[0] + "!"} arithmetic.
     */
    public static long _abi_str_char_at(long text, long index) {
        String s = readString(text);
        long at = index < 0 ? index + s.length() : index;
        if (at < 0 || at >= s.length()) {
            _abi_raise(allocateString("IndexError: string index out of range"), 0);
        }
        return allocateString(String.valueOf(s.charAt((int) at)));
    }

    public static long _abi_str_slice(long text, long start, long stop) {
        String s = readString(text);
        int from = (int) clampIndex(start, s.length());
        int to = (int) clampIndex(stop, s.length());
        return allocateString(from >= to ? "" : s.substring(from, to));
    }

    public static long _abi_str_slice_step(long text, long start, long stop, long step) {
        String s = readString(text);
        StringBuilder out = new StringBuilder();
        if (step == 0) {
            _abi_raise(allocateString("ValueError: slice step cannot be zero"), 0);
        }
        if (step > 0) {
            for (long i = clampIndex(start, s.length()); i < clampIndex(stop, s.length()); i += step) {
                out.append(s.charAt((int) i));
            }
        } else {
            long from = start < 0 ? start + s.length() : Math.min(start, s.length() - 1);
            long to = stop < 0 ? stop + s.length() : stop;
            for (long i = from; i > to && i >= 0; i += step) {
                out.append(s.charAt((int) i));
            }
        }
        return allocateString(out.toString());
    }

    private static long clampIndex(long index, long length) {
        long i = index < 0 ? index + length : index;
        if (i < 0) {
            return 0;
        }
        return Math.min(i, length);
    }

    public static long _abi_str_replace(long text, long old, long replacement) {
        return allocateString(
                readString(text).replace(readString(old), readString(replacement)));
    }

    /**
     * {@code _abi_str_split(str, separator)} — a list of string pointers.
     *
     * <p>Java's split takes a regex and drops trailing empties; Python's takes a
     * literal and keeps them. {@code "a,b,,".split(",")} is four elements in
     * Python and two in Java, so this is written out rather than delegated.
     */
    public static long _abi_str_split(long text, long separator) {
        String s = readString(text);
        String sep = readString(separator);
        long result = _abi_new_list(4);
        if (sep.isEmpty()) {
            _abi_raise(allocateString("ValueError: empty separator"), 0);
        }
        int from = 0;
        while (true) {
            int at = s.indexOf(sep, from);
            if (at < 0) {
                _abi_list_append(result, allocateString(s.substring(from)));
                return result;
            }
            _abi_list_append(result, allocateString(s.substring(from, at)));
            from = at + sep.length();
        }
    }

    /** split() with no argument: any whitespace run, no empty pieces. */
    public static long _abi_str_split_ws(long text) {
        long result = _abi_new_list(4);
        for (String piece : readString(text).trim().split("\\s+")) {
            if (!piece.isEmpty()) {
                _abi_list_append(result, allocateString(piece));
            }
        }
        return result;
    }

    public static long _abi_str_splitlines(long text) {
        long result = _abi_new_list(4);
        String s = readString(text);
        if (s.isEmpty()) {
            return result;
        }
        for (String line : s.split("\n", -1)) {
            _abi_list_append(result, allocateString(line));
        }
        // A trailing newline does not make a final empty line in Python.
        if (s.endsWith("\n")) {
            storeLong(result + LIST_LEN_OFF, listLength(result) - 1);
        }
        return result;
    }

    /** {@code _abi_str_join(separator, list)} — separator first, as self. */
    public static long _abi_str_join(long separator, long list) {
        String sep = readString(separator);
        StringBuilder out = new StringBuilder();
        long length = listLength(list);
        for (long i = 0; i < length; i++) {
            if (i > 0) {
                out.append(sep);
            }
            out.append(readString(listGet(list, i)));
        }
        return allocateString(out.toString());
    }

    public static long _abi_str_index_of(long haystack, long needle) {
        return readString(haystack).indexOf(readString(needle));
    }

    public static long _abi_str_count(long haystack, long needle) {
        String s = readString(haystack);
        String find = readString(needle);
        if (find.isEmpty()) {
            return s.length() + 1;
        }
        long count = 0;
        for (int at = s.indexOf(find); at >= 0; at = s.indexOf(find, at + find.length())) {
            count++;
        }
        return count;
    }

    public static long _abi_str_eq(long left, long right) {
        return readString(left).equals(readString(right)) ? 1 : 0;
    }

    public static long _abi_str_cmp(long left, long right) {
        int order = readString(left).compareTo(readString(right));
        return order < 0 ? -1 : (order > 0 ? 1 : 0);
    }

    public static long _abi_str_starts_with(long text, long prefix) {
        return readString(text).startsWith(readString(prefix)) ? 1 : 0;
    }

    public static long _abi_str_ends_with(long text, long suffix) {
        return readString(text).endsWith(readString(suffix)) ? 1 : 0;
    }

    public static long _abi_str_removeprefix(long text, long prefix) {
        String s = readString(text);
        String p = readString(prefix);
        return allocateString(s.startsWith(p) ? s.substring(p.length()) : s);
    }

    public static long _abi_str_removesuffix(long text, long suffix) {
        String s = readString(text);
        String p = readString(suffix);
        return allocateString(s.endsWith(p) ? s.substring(0, s.length() - p.length()) : s);
    }

    public static long _abi_str_capitalize(long text) {
        String s = readString(text);
        return allocateString(s.isEmpty()
                ? s
                : Character.toUpperCase(s.charAt(0)) + s.substring(1).toLowerCase());
    }

    public static long _abi_str_swapcase(long text) {
        StringBuilder out = new StringBuilder();
        for (char c : readString(text).toCharArray()) {
            out.append(Character.isUpperCase(c) ? Character.toLowerCase(c)
                                                : Character.toUpperCase(c));
        }
        return allocateString(out.toString());
    }

    public static long _abi_str_title(long text) {
        StringBuilder out = new StringBuilder();
        boolean startOfWord = true;
        for (char c : readString(text).toCharArray()) {
            out.append(startOfWord ? Character.toUpperCase(c) : Character.toLowerCase(c));
            startOfWord = !Character.isLetterOrDigit(c);
        }
        return allocateString(out.toString());
    }

    public static long _abi_str_ljust(long text, long width, long fill) {
        return allocateString(pad(readString(text), width, (char) fill, true));
    }

    public static long _abi_str_rjust(long text, long width, long fill) {
        return allocateString(pad(readString(text), width, (char) fill, false));
    }

    public static long _abi_str_zfill(long text, long width) {
        String s = readString(text);
        String sign = (s.startsWith("-") || s.startsWith("+")) ? s.substring(0, 1) : "";
        String digits = s.substring(sign.length());
        return allocateString(sign + pad(digits, width - sign.length(), '0', false));
    }

    private static String pad(String text, long width, char fill, boolean onRight) {
        StringBuilder out = new StringBuilder();
        for (long i = text.length(); i < width; i++) {
            out.append(fill);
        }
        return onRight ? text + out : out + text;
    }

    public static long _abi_str_isdigit(long text) {
        return allCharsAre(readString(text), 'd') ? 1 : 0;
    }

    public static long _abi_str_isalpha(long text) {
        return allCharsAre(readString(text), 'a') ? 1 : 0;
    }

    public static long _abi_str_isalnum(long text) {
        return allCharsAre(readString(text), 'n') ? 1 : 0;
    }

    public static long _abi_str_isspace(long text) {
        return allCharsAre(readString(text), 's') ? 1 : 0;
    }

    public static long _abi_str_islower(long text) {
        String s = readString(text);
        return (!s.equals(s.toUpperCase()) && s.equals(s.toLowerCase())) ? 1 : 0;
    }

    public static long _abi_str_isupper(long text) {
        String s = readString(text);
        return (!s.equals(s.toLowerCase()) && s.equals(s.toUpperCase())) ? 1 : 0;
    }

    /** Python's is*() are all false for the empty string, hence the guard. */
    private static boolean allCharsAre(String text, char kind) {
        if (text.isEmpty()) {
            return false;
        }
        for (char c : text.toCharArray()) {
            boolean ok;
            switch (kind) {
                case 'd': ok = Character.isDigit(c); break;
                case 'a': ok = Character.isLetter(c); break;
                case 'n': ok = Character.isLetterOrDigit(c); break;
                default:  ok = Character.isWhitespace(c); break;
            }
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    /** {@code int(str)} — raises ValueError, exactly as Python does. */
    public static long _abi_str_to_int(long text) {
        String s = readString(text).trim();
        try {
            return Long.parseLong(s);
        } catch (NumberFormatException e) {
            _abi_raise(allocateString(
                    "ValueError: invalid literal for int() with base 10: '" + s + "'"), 0);
            return 0;
        }
    }

    public static long _abi_str_to_int_base(long text, long base) {
        String s = readString(text).trim();
        try {
            return Long.parseLong(s, (int) base);
        } catch (NumberFormatException e) {
            _abi_raise(allocateString(
                    "ValueError: invalid literal for int() with base " + base + ": '" + s + "'"), 0);
            return 0;
        }
    }

    public static long _abi_chr(long code) {
        return allocateString(String.valueOf((char) code));
    }

    // ======================================================================
    // number formatting
    // ======================================================================

    /**
     * {@code _abi_int_to_base(value, base, scratch)} — a FRESH string, not a
     * write into {@code scratch}.
     *
     * <p>The lowering hands every conversion in a statement the same global
     * scratch buffer, so {@code print(a, b, c)} passes three pointers that are
     * all one address. Writing in place makes every one of them show the last
     * value converted; the native runtime returns independent results, and this
     * has to match it.
     */
    public static long _abi_int_to_base(long value, long base, long scratch) {
        return allocateString(base == 10
                ? Long.toString(value)
                : Long.toString(value, (int) base));
    }

    public static long _abi_int_to_str(long value) {
        return allocateString(Long.toString(value));
    }

    public static long _abi_int_to_binary(long value) {
        return allocateString(Long.toBinaryString(value));
    }

    /**
     * {@code _abi_float_to_str(value)} — CPython's {@code str()}, which is not
     * {@link Double#toString}: Python prints 1e+16 where Java prints 1.0E16,
     * and 'inf'/'nan' where Java prints 'Infinity'/'NaN'.
     */
    public static long _abi_float_to_str(double value) {
        return allocateString(floatToString(value));
    }

    private static String floatToString(double value) {
        if (Double.isNaN(value)) {
            return "nan";
        }
        if (Double.isInfinite(value)) {
            return value > 0 ? "inf" : "-inf";
        }
        if (value == Math.floor(value) && Math.abs(value) < 1e16) {
            return (long) value + ".0";
        }
        String text = Double.toString(value);
        // Java's E-notation differs from Python's in both case and sign.
        if (text.indexOf('E') >= 0) {
            text = text.replace("E-", "e-");
            text = text.replace("E", "e+");
        }
        return text;
    }

    // ======================================================================
    // repr
    // ======================================================================

    /**
     * Element kinds, as the lowering encodes them: the low four bits are the
     * base kind and the rest is the inner kind of a container, so a
     * {@code list[list[int]]} arrives as one packed long.
     */
    private static final int KIND_INT = 0;
    private static final int KIND_STR = 1;
    private static final int KIND_FLOAT = 2;
    private static final int KIND_LIST = 3;
    private static final int KIND_DICT = 4;
    private static final int KIND_ITEMS = 5;

    /**
     * {@code _abi_fmt_elem(value, kind)} — one element as it appears INSIDE a
     * container, which is repr and not str: {@code print(["a"])} shows
     * {@code ['a']} with the quotes, while {@code print("a")} does not.
     */
    public static long _abi_fmt_elem(long value, long kind) {
        return allocateString(formatElement(value, kind));
    }

    private static String formatElement(long value, long kind) {
        switch ((int) (kind & 0xF)) {
            case KIND_STR:
                return "'" + readString(value) + "'";
            case KIND_FLOAT:
                return floatToString(Double.longBitsToDouble(value));
            case KIND_LIST:
                return listToString(value, kind >> 4);
            case KIND_DICT:
                return dictToString(value, KIND_STR, kind >> 4);
            case KIND_ITEMS:
                return "(" + formatElement(loadLong(value), KIND_STR) + ", "
                        + formatElement(loadLong(value + 8), kind >> 4) + ")";
            case KIND_INT:
            default:
                return Long.toString(value);
        }
    }

    /** {@code _abi_list_repr(list, elementKind)}. */
    public static long _abi_list_repr(long list, long elementKind) {
        return allocateString(listToString(list, elementKind));
    }

    private static String listToString(long list, long elementKind) {
        StringBuilder out = new StringBuilder("[");
        long length = listLength(list);
        for (long i = 0; i < length; i++) {
            if (i > 0) {
                out.append(", ");
            }
            out.append(formatElement(listGet(list, i), elementKind));
        }
        return out.append("]").toString();
    }

    /** {@code _abi_dict_repr(dict, keyKind, valueKind)}. */
    public static long _abi_dict_repr(long dict, long keyKind, long valueKind) {
        return allocateString(dictToString(dict, keyKind, valueKind));
    }

    private static String dictToString(long dict, long keyKind, long valueKind) {
        StringBuilder out = new StringBuilder("{");
        long length = loadLong(dict + DICT_LEN_OFF);
        long order = loadLong(dict + DICT_ORDER_OFF);
        for (long i = 0; i < length; i++) {
            if (i > 0) {
                out.append(", ");
            }
            long key = loadLong(order + i * 8);
            out.append(formatElement(key, keyKind)).append(": ");
            out.append(formatElement(_abi_dict_get_default(dict, key, 0), valueKind));
        }
        return out.append("}").toString();
    }

    /** {@code _abi_set_repr(set, elementKind)} — a set is a dict of keys. */
    public static long _abi_set_repr(long set, long elementKind) {
        long length = loadLong(set + DICT_LEN_OFF);
        if (length == 0) {
            return allocateString("set()");
        }
        StringBuilder out = new StringBuilder("{");
        long order = loadLong(set + DICT_ORDER_OFF);
        for (long i = 0; i < length; i++) {
            if (i > 0) {
                out.append(", ");
            }
            out.append(formatElement(loadLong(order + i * 8), elementKind));
        }
        return allocateString(out.append("}").toString());
    }

    // ======================================================================
    // numbers
    // ======================================================================

    public static long _abi_divmod(long left, long right) {
        // Python floors and takes the sign of the divisor; Java truncates.
        long quotient = Math.floorDiv(left, right);
        long remainder = Math.floorMod(left, right);
        long pair = allocate(16);
        storeLong(pair, quotient);
        storeLong(pair + 8, remainder);
        return pair;
    }

    /**
     * {@code round(x)} — banker's rounding, which is what Python does and what
     * {@link Math#round} does not: Python's round(2.5) is 2, not 3.
     */
    public static double _abi_round_f64(double value) {
        return Math.rint(value);
    }

    public static double _abi_round_f64(double value, long digits) {
        double scale = Math.pow(10, digits);
        return Math.rint(value * scale) / scale;
    }

    // ---- the C library the lowering calls straight through to --------------
    //
    // The native backend links these from libc/libm. Standing in for them is
    // part of being a backend, not an extra: `abs()` lowering to `labs` is not
    // something this backend gets to opt out of.

    public static long labs(long value) {
        return Math.abs(value);
    }

    public static double fabs(double value) {
        return Math.abs(value);
    }

    public static double pow(double base, double exponent) {
        return Math.pow(base, exponent);
    }

    public static double sqrt(double value) {
        return Math.sqrt(value);
    }

    public static double floor(double value) {
        return Math.floor(value);
    }

    public static double ceil(double value) {
        return Math.ceil(value);
    }

    public static double fmod(double left, double right) {
        return left % right;
    }

    public static double sin(double value) {
        return Math.sin(value);
    }

    public static double cos(double value) {
        return Math.cos(value);
    }

    public static double tan(double value) {
        return Math.tan(value);
    }

    public static double atan2(double y, double x) {
        return Math.atan2(y, x);
    }

    public static double log(double value) {
        return Math.log(value);
    }

    public static double log2(double value) {
        return Math.log(value) / Math.log(2);
    }

    public static double log10(double value) {
        return Math.log10(value);
    }

    public static double exp(double value) {
        return Math.exp(value);
    }

    public static double _abi_fmax_f64(double a, double b) {
        return Math.max(a, b);
    }

    public static double _abi_fmin_f64(double a, double b) {
        return Math.min(a, b);
    }

    // Errors: `_abi_raise` is inherited from Containers, which needs it too
    // (an out-of-range pop raises). Redeclaring it here would HIDE that one
    // rather than override it — statics do not dispatch — so the two copies
    // could drift apart while looking identical.

    // ======================================================================
    // output
    // ======================================================================

    /**
     * {@code printf(format, args)} — one entry point, not a set of overloads.
     *
     * <p>The lowering emits printf with as many arguments as the statement
     * happens to have, so any fixed set of arities is one longer `print` away
     * from a NoSuchMethodError. The codegen packs the tail into a {@code long[]}
     * instead (see {@code emit_variadic_call}), which is also what a C varargs
     * call does: every argument arrives as a raw 64-bit word, floats included.
     */
    public static void printf(long format, long[] args) {
        OUT.print(format(readString(format), args));
    }

    /**
     * A deliberately small printf: enough of the C format language for what the
     * lowering actually emits ({@code %s}, {@code %d}/{@code %ld}, {@code %c},
     * {@code %f}, {@code %x}, {@code %%}), rather than a general one.
     */
    private static String format(String spec, long[] args) {
        StringBuilder out = new StringBuilder();
        int argIndex = 0;
        for (int i = 0; i < spec.length(); i++) {
            char c = spec.charAt(i);
            if (c != '%') {
                out.append(c);
                continue;
            }
            i++;
            if (i >= spec.length()) {
                break;
            }
            // Skip width/length modifiers: %ld, %5d, %zu ...
            while (i < spec.length() && "0123456789lzhu.-+ ".indexOf(spec.charAt(i)) >= 0
                    && "diouxXeEfgGcsp%".indexOf(spec.charAt(i)) < 0) {
                i++;
            }
            if (i >= spec.length()) {
                break;
            }
            char kind = spec.charAt(i);
            if (kind == '%') {
                out.append('%');
                continue;
            }
            long arg = argIndex < args.length ? args[argIndex] : 0L;
            argIndex++;
            switch (kind) {
                case 's': out.append(readString(arg)); break;
                case 'c': out.append((char) arg); break;
                case 'f':
                case 'g':
                case 'e': out.append(floatToString(Double.longBitsToDouble(arg))); break;
                case 'x': out.append(Long.toHexString(arg)); break;
                default:  out.append(arg); break;
            }
        }
        return out.toString();
    }

    public static void puts(long address) {
        OUT.println(readString(address));
    }

    public static long _abi_input(long prompt) {
        OUT.print(readString(prompt));
        OUT.flush();
        try {
            java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.InputStreamReader(System.in));
            String line = reader.readLine();
            return allocateString(line == null ? "" : line);
        } catch (java.io.IOException e) {
            return allocateString("");
        }
    }

    public static void exit(long code) {
        OUT.flush();
        System.exit((int) code);
    }
}
