#!/usr/bin/env python3
"""Bounded, non-executing ELF dynamic/ARM metadata inspection (stdlib only)."""
import argparse
import json
import pathlib
import struct

MAX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 200000


class ELFError(ValueError):
    pass


class ELF:
    def __init__(self, data):
        self.data = data
        if len(data) > MAX_BYTES or len(data) < 16 or data[:4] != b'\x7fELF':
            raise ELFError('invalid_elf')
        if data[4] not in (1, 2) or data[5] not in (1, 2) or data[6] != 1:
            raise ELFError('unsupported_elf_encoding')
        self.bits = 32 if data[4] == 1 else 64
        self.order = '<' if data[5] == 1 else '>'
        h = self.unpack('HHIIIIIHHHHHH' if self.bits == 32 else 'HHIQQQIHHHHHH', 16)
        self.machine, offset, size, count, strings = h[1], h[5], h[10], h[11], h[12]
        fmt = 'IIIIIIIIII' if self.bits == 32 else 'IIQQQQIIQQ'
        if not count or count > 8192 or size != struct.calcsize(self.order + fmt):
            raise ELFError('unsupported_section_table')
        self.sections = [self.unpack(fmt, offset + i * size) for i in range(count)]
        if strings >= count:
            raise ELFError('invalid_section_names')
        names = self.section(strings)
        self.names = [self.string(names, s[0]) for s in self.sections]
        for s in self.sections:
            if s[1] != 8:  # NOBITS occupies memory, not file bytes.
                self.slice(s[4], s[5])

    def slice(self, offset, size):
        if offset < 0 or size < 0 or offset > len(self.data) - size:
            raise ELFError('truncated_elf')
        return self.data[offset:offset + size]

    def unpack(self, fmt, offset):
        return struct.unpack(self.order + fmt, self.slice(offset, struct.calcsize(self.order + fmt)))

    def section(self, index):
        if not 0 <= index < len(self.sections):
            raise ELFError('invalid_section_link')
        s = self.sections[index]
        return self.slice(s[4], s[5])

    @staticmethod
    def string(data, offset):
        if not 0 <= offset < len(data):
            raise ELFError('invalid_string_offset')
        end = data.find(b'\0', offset, min(len(data), offset + 4097))
        if end < 0:
            raise ELFError('unterminated_string')
        try:
            return data[offset:end].decode('ascii')
        except UnicodeDecodeError:
            raise ELFError('non_ascii_metadata') from None

    def entries(self, index, fmt):
        s = self.sections[index]
        size = struct.calcsize(self.order + fmt)
        if s[9] != size or s[5] % size or s[5] // size > MAX_ENTRIES:
            raise ELFError('invalid_entry_size')
        return [self.unpack(fmt, p) for p in range(s[4], s[4] + s[5], size)]

    def versions(self):
        result = {}
        for i, section in enumerate(self.sections):
            if section[1] not in (0x6ffffffe, 0x6ffffffd):
                continue
            data, strings = self.section(i), self.section(section[6])
            offset, seen = 0, set()
            while offset < len(data):
                if offset in seen or len(seen) >= 65536:
                    raise ELFError('invalid_version_chain')
                seen.add(offset)
                def read(fmt, pos):
                    size = struct.calcsize(self.order + fmt)
                    if pos < 0 or pos + size > len(data):
                        raise ELFError('truncated_version')
                    return struct.unpack_from(self.order + fmt, data, pos)
                if section[1] == 0x6ffffffe:
                    version, count, _, aux, nxt = read('HHIII', offset)
                    pos = offset + aux
                    if version != 1 or count > 4096:
                        raise ELFError('invalid_version_need')
                    for j in range(count):
                        _, _, index, name, step = read('IHHII', pos)
                        result[index & 0x7fff] = self.string(strings, name)
                        if j + 1 < count and step < 16:
                            raise ELFError('invalid_version_aux')
                        pos += step
                else:
                    version, _, index, _, _, aux, nxt = read('HHHHIII', offset)
                    if version != 1:
                        raise ELFError('invalid_version_definition')
                    name, _ = read('II', offset + aux)
                    result[index & 0x7fff] = self.string(strings, name)
                if not nxt:
                    break
                if nxt < 16 or offset + nxt >= len(data):
                    raise ELFError('invalid_version_next')
                offset += nxt
        return result

    def inspect(self):
        versions, needed, undefined, exported = self.versions(), [], {}, []
        for i, s in enumerate(self.sections):
            if s[1] == 6:
                strings = self.section(s[6])
                for tag, value in self.entries(i, 'iI' if self.bits == 32 else 'qQ'):
                    if tag == 0:
                        break
                    if tag == 1:
                        needed.append(self.string(strings, value))
            if s[1] != 11:
                continue
            strings = self.section(s[6])
            symbols = self.entries(i, 'IIIBBH' if self.bits == 32 else 'IBBHQQ')
            ver = next((self.section(j) for j, x in enumerate(self.sections)
                        if x[1] == 0x6fffffff and x[6] == i), None)
            if ver is not None and len(ver) != 2 * len(symbols):
                raise ELFError('invalid_symbol_versions')
            for n, symbol in enumerate(symbols):
                name = self.string(strings, symbol[0])
                info, shndx = (symbol[3], symbol[5]) if self.bits == 32 else (symbol[1], symbol[3])
                binding = info >> 4
                # STB_GNU_UNIQUE is a dynamic definition, not an undefined demand.
                if not name or (binding not in (1, 2) and not (binding == 10 and shndx != 0)):
                    continue
                index = struct.unpack_from(self.order + 'H', ver, n * 2)[0] & 0x7fff if ver else 1
                if index > 1 and index not in versions:
                    raise ELFError('unknown_symbol_version')
                version = versions[index] if index > 1 else 'unversioned'
                if shndx == 0:
                    undefined.setdefault(version, []).append({'name': name, 'weak': binding == 2})
                else:
                    visibility = symbol[4] if self.bits == 32 else symbol[2]
                    if visibility & 3 in (0, 3):
                        exported.append({'name': name, 'version': version})
        attrs = []
        for i, s in enumerate(self.sections):
            if s[1] == 0x70000003:
                attrs.extend(arm_attributes(self.section(i), self.order))
        return {'class': self.bits, 'machine': {40: 'ARM', 183: 'AArch64', 62: 'x86-64'}.get(self.machine, str(self.machine)),
                'machine_id': self.machine, 'endianness': 'little' if self.order == '<' else 'big',
                'arm_attributes': attrs, 'needed': sorted(set(needed)),
                'undefined_by_version': {v: sorted(rows, key=lambda x: x['name']) for v, rows in sorted(undefined.items())},
                'exports': sorted(exported, key=lambda x: (x['name'], x['version']))}


def arm_attributes(data, order):
    if not data or data[0] != 65:
        raise ELFError('invalid_arm_attributes')
    def integer(pos, end):
        value = 0
        for shift in range(0, 70, 7):
            if pos >= end:
                break
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value, pos
        raise ELFError('invalid_uleb128')
    def string(pos, end):
        stop = data.find(b'\0', pos, end)
        if stop < 0:
            raise ELFError('invalid_attribute_string')
        return stop + 1
    result, start = [], 1
    while start < len(data):
        if start + 4 > len(data):
            raise ELFError('truncated_attribute_vendor')
        size = struct.unpack_from(order + 'I', data, start)[0]
        end = start + size
        if size < 5 or end > len(data):
            raise ELFError('invalid_attribute_vendor')
        pos = string(start + 4, end)
        vendor = data[start + 4:pos - 1]
        if vendor == b'aeabi':
            while pos < end:
                substart = pos
                tag, pos = integer(pos, end)
                if pos + 4 > end:
                    raise ELFError('truncated_attribute_subsection')
                size = struct.unpack_from(order + 'I', data, pos)[0]
                subend = substart + size
                pos += 4
                if subend < pos or subend > end:
                    raise ELFError('invalid_attribute_subsection')
                if tag == 1:
                    attrs = {}
                    while pos < subend:
                        tag, pos = integer(pos, subend)
                        if tag in (4, 5, 67) or (tag > 32 and tag % 2):
                            pos = string(pos, subend)
                        else:
                            value, pos = integer(pos, subend)
                            if tag == 32:
                                pos = string(pos, subend)
                            if tag in (6, 28):
                                attrs[{6: 'Tag_CPU_arch', 28: 'Tag_ABI_VFP_args'}[tag]] = value
                    result.append(attrs)
                pos = subend
        start = end
    return result


def inspect_path(path):
    with pathlib.Path(path).open('rb') as stream:
        return ELF(stream.read(MAX_BYTES + 1)).inspect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_path(args.library), sort_keys=True))
    except (OSError, ELFError) as exc:
        parser.exit(1, json.dumps({'error': str(exc) if isinstance(exc, ELFError) else 'file_unavailable'}) + '\n')


if __name__ == '__main__':
    main()
