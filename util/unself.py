import os
import struct
import sys


MAGICS = {0x1D3D154F: "PS4", 0xEEF51454: "PS5"}


def unself(data):
    segment_count = struct.unpack_from("<H", data, 0x18)[0]
    elf_offset = 0x20 + 0x20 * segment_count
    if data[elf_offset:elf_offset + 4] != b"\x7fELF":
        raise ValueError("no ELF header where expected")
    program_header_offset, = struct.unpack_from("<Q", data, elf_offset + 0x20)
    entry_size, entry_count = struct.unpack_from("<HH", data, elf_offset + 0x36)

    program_headers = []
    for index in range(entry_count):
        fields = struct.unpack_from(
            "<IIQQQQQQ",
            data,
            elf_offset + program_header_offset + index * entry_size,
        )
        program_headers.append((fields[2], fields[5]))

    header_end = program_header_offset + entry_count * entry_size
    output_end = max([header_end] + [offset + size for offset, size in program_headers])
    output = bytearray(output_end)
    output[:header_end] = data[elf_offset:elf_offset + header_end]

    for index in range(segment_count):
        flags, offset, _compressed_size, data_size = struct.unpack_from(
            "<QQQQ", data, 0x20 + index * 0x20
        )
        if not flags & 0x800:
            continue
        if flags & 0xA:
            raise ValueError(f"segment {index} is encrypted/compressed")
        program_offset, program_size = program_headers[(flags >> 20) & 0xFFF]
        size = min(data_size, program_size)
        output[program_offset:program_offset + size] = data[offset:offset + size]
    return output


def handle(path):
    """Returns "ok", "fail" or "skip"."""
    with open(path, "rb") as source:
        header = source.read(4)
        if len(header) < 4 or struct.unpack("<I", header)[0] not in MAGICS:
            return "skip"
        data = header + source.read()
    kind = MAGICS[struct.unpack_from("<I", data, 0)[0]]
    temporary_path = path + ".tmp"
    try:
        output = unself(data)
        with open(temporary_path, "wb") as destination:
            destination.write(output)
        os.replace(temporary_path, path)
        print("OK  ", kind, path)
        return "ok"
    except Exception as error:
        print("FAIL", kind, path, "-", error, file=sys.stderr)
        return "fail"
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def run_task(folder):
    counts = {"ok": 0, "fail": 0, "skip": 0}
    for directory, _, files in os.walk(folder):
        for filename in files:
            counts[handle(os.path.join(directory, filename))] += 1
    print(
        f"\n{counts['ok']} converted, {counts['fail']} failed, "
        f"{counts['skip']} skipped"
    )
