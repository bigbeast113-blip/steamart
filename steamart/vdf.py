"""Readers and writers for Valve Data Format files, standard library only.

Two dialects show up in a Steam install and we need both:

* the *binary* format used by ``userdata/<id>/config/shortcuts.vdf``, which is
  where non-Steam games live;
* the *text* format used by ``config/config.vdf`` and ``config/loginusers.vdf``,
  which is where the Proton compatibility-tool mapping lives.
"""

from __future__ import annotations

import struct
from collections import OrderedDict

# Binary VDF type markers.
_MAP = 0x00
_STRING = 0x01
_INT32 = 0x02
_FLOAT32 = 0x03
_POINTER = 0x04
_WIDESTRING = 0x05
_COLOR = 0x06
_UINT64 = 0x07
_END = 0x08
_INT64 = 0x0A
_END_ALT = 0x0B


class VDFError(ValueError):
    """Raised when a VDF file cannot be parsed."""


class UInt64(int):
    """An int that must round-trip as a binary VDF uint64 rather than int32."""


class Int64(int):
    """An int that must round-trip as a binary VDF int64."""


# --------------------------------------------------------------------------
# binary
# --------------------------------------------------------------------------

def _read_cstring(data, pos):
    end = data.find(b"\x00", pos)
    if end < 0:
        raise VDFError("unterminated string at offset %d" % pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def _read_wstring(data, pos):
    end = pos
    while True:
        end = data.find(b"\x00\x00", end)
        if end < 0:
            raise VDFError("unterminated wide string at offset %d" % pos)
        if (end - pos) % 2 == 0:
            break
        end += 1
    return data[pos:end].decode("utf-16-le", "replace"), end + 2


def _read_map(data, pos):
    out = OrderedDict()
    size = len(data)
    while True:
        if pos >= size:
            # Some writers omit the final terminator; treat EOF as end-of-map.
            return out, pos
        marker = data[pos]
        pos += 1
        if marker in (_END, _END_ALT):
            return out, pos
        key, pos = _read_cstring(data, pos)
        if marker == _MAP:
            value, pos = _read_map(data, pos)
        elif marker == _STRING:
            value, pos = _read_cstring(data, pos)
        elif marker == _WIDESTRING:
            value, pos = _read_wstring(data, pos)
        elif marker in (_INT32, _POINTER, _COLOR):
            value = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        elif marker == _FLOAT32:
            value = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        elif marker == _UINT64:
            value = UInt64(struct.unpack_from("<Q", data, pos)[0])
            pos += 8
        elif marker == _INT64:
            value = Int64(struct.unpack_from("<q", data, pos)[0])
            pos += 8
        else:
            raise VDFError(
                "unknown binary VDF marker 0x%02x at offset %d" % (marker, pos - 1)
            )
        out[key] = value


def binary_loads(data):
    """Parse a binary VDF blob into nested OrderedDicts."""
    obj, _ = _read_map(data, 0)
    return obj


def binary_load(path):
    with open(path, "rb") as fh:
        return binary_loads(fh.read())


def _clamp_i32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def _write_map(out, mapping):
    for key, value in mapping.items():
        kb = str(key).encode("utf-8") + b"\x00"
        if isinstance(value, dict):
            out += bytes([_MAP]) + kb
            _write_map(out, value)
            out += bytes([_END])
        elif isinstance(value, UInt64):
            out += bytes([_UINT64]) + kb + struct.pack("<Q", int(value) & 0xFFFFFFFFFFFFFFFF)
        elif isinstance(value, Int64):
            out += bytes([_INT64]) + kb + struct.pack("<q", int(value))
        elif isinstance(value, bool):
            out += bytes([_INT32]) + kb + struct.pack("<i", 1 if value else 0)
        elif isinstance(value, int):
            out += bytes([_INT32]) + kb + struct.pack("<i", _clamp_i32(value))
        elif isinstance(value, float):
            out += bytes([_FLOAT32]) + kb + struct.pack("<f", value)
        else:
            out += bytes([_STRING]) + kb + str(value).encode("utf-8") + b"\x00"


def binary_dumps(mapping):
    out = bytearray()
    _write_map(out, mapping)
    out += bytes([_END])
    return bytes(out)


def binary_dump(path, mapping):
    with open(path, "wb") as fh:
        fh.write(binary_dumps(mapping))


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif ch in "{}":
            tokens.append((ch, True))
            i += 1
        elif ch == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(_ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            tokens.append(("".join(buf), False))
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n"{}':
                i += 1
            tokens.append((text[start:i], False))
    return tokens


def _parse_pairs(tokens, i, out, top_level=False):
    while i < len(tokens):
        token, is_punct = tokens[i]
        if is_punct and token == "}":
            if top_level:
                raise VDFError("unexpected '}' at top level")
            return i + 1
        if is_punct:
            raise VDFError("unexpected '%s' where a key was expected" % token)
        key = token
        i += 1
        if i >= len(tokens):
            raise VDFError("key '%s' has no value" % key)
        value, v_punct = tokens[i]
        if v_punct and value == "{":
            child = OrderedDict()
            i = _parse_pairs(tokens, i + 1, child)
            out[key] = child
        else:
            out[key] = value
            i += 1
    if not top_level:
        raise VDFError("unclosed block")
    return i


def text_loads(text):
    """Parse a text VDF document into nested OrderedDicts."""
    tokens = _tokenize(text)
    root = OrderedDict()
    index = _parse_pairs(tokens, 0, root, top_level=True)
    if index != len(tokens):
        raise VDFError("trailing data in text VDF")
    return root


def text_load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return text_loads(fh.read())


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def text_dumps(mapping, indent=0):
    pad = "\t" * indent
    lines = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            lines.append('%s"%s"' % (pad, _escape(key)))
            lines.append("%s{" % pad)
            body = text_dumps(value, indent + 1)
            if body:
                lines.append(body)
            lines.append("%s}" % pad)
        else:
            lines.append('%s"%s"\t\t"%s"' % (pad, _escape(key), _escape(value)))
    return "\n".join(lines)


def text_dump(path, mapping):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text_dumps(mapping) + "\n")


def get_ci(mapping, key, default=None):
    """Case-insensitive lookup. Steam is inconsistent about key casing."""
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for existing, value in mapping.items():
        if existing.lower() == lowered:
            return value
    return default


def setdefault_ci(mapping, key):
    """Case-insensitive ``setdefault`` that creates a nested dict."""
    for existing, value in mapping.items():
        if existing.lower() == key.lower():
            if not isinstance(value, dict):
                mapping[existing] = OrderedDict()
            return mapping[existing]
    mapping[key] = OrderedDict()
    return mapping[key]
