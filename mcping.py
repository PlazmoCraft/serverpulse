import asyncio
import json
import struct


def write_varint(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


async def read_varint(reader):
    result = 0
    shift = 0
    while True:
        b = await reader.readexactly(1)
        val = b[0]
        result |= (val & 0x7F) << shift
        if not (val & 0x80):
            return result
        shift += 7
        if shift > 35:
            raise ValueError("Слишком длинный VarInt")


def write_string(text):
    data = text.encode("utf-8")
    return write_varint(len(data)) + data


async def ping_server(host, port=25565, timeout=10, protocol=-1):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        handshake = (
            b"\x00"
            + write_varint(protocol)
            + write_string(host)
            + struct.pack(">H", port)
            + write_varint(1)
        )
        writer.write(write_varint(len(handshake)) + handshake)
        await writer.drain()

        status = b"\x00"
        writer.write(write_varint(len(status)) + status)
        await writer.drain()

        length = await read_varint(reader)
        payload = await reader.readexactly(length)
        pos = 0
        packet_id = 0
        while payload[pos] & 0x80:
            packet_id = (packet_id << 7) | (payload[pos] & 0x7F)
            pos += 1
        packet_id = (packet_id << 7) | (payload[pos] & 0x7F)
        pos += 1

        slen = 0
        shift = 0
        while payload[pos] & 0x80:
            slen |= (payload[pos] & 0x7F) << shift
            pos += 1
            shift += 7
        slen |= (payload[pos] & 0x7F) << shift
        pos += 1

        raw = payload[pos : pos + slen].decode("utf-8", errors="replace")
        return json.loads(raw)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def fetch_status(host, port=25565):
    try:
        data = await asyncio.wait_for(ping_server(host, port, protocol=775), timeout=12)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        data = await asyncio.wait_for(ping_server(host, port, protocol=-1), timeout=12)
    return data