# 此文件包含针对已知混淆技术的已记录策略以及
# 测试这些策略的一些机制。

# 架构非常简单。解包rpyc格式至少有两个步骤。
# RPYC2是一种存档格式，可以包含多个流（称为槽位）
# 第一步是从中提取槽位，这由提取器之一完成。
# 这些都给出一个blob，要么仍然是zlib压缩的，要么只是原始槽位
# （某些方法依赖于zlib压缩来确定槽位长度）
# 然后，有0个或多个解密该槽位数据的步骤。这通常最终
# 是base64、字符串转义、十六进制编码、zlib压缩等的层层嵌套。
# 我们通过检查它们是否适合来处理这个问题。

import base64
import struct
import zlib
from collections import Counter

from decompiler.renpycompat import pickle_safe_loads

# 提取器是简单的函数(fobj, slotno) -> bytes
# 如果失败，它们会引发ValueError
EXTRACTORS = []
def extractor(f):
    EXTRACTORS.append(f)
    return f

# 解密器是简单的函数(bytes, Counter) -> bytes
# 如果失败，它们返回None。如果返回输入，也被认为失败。
DECRYPTORS = []
def decryptor(f):
    DECRYPTORS.append(f)
    return f


# 在此处添加游戏特定的自定义提取/解密逻辑

# 自定义提取/解密逻辑结束


@extractor
def extract_slot_rpyc(f, slot):
    """
    用于实际rpyc格式文件的槽位提取器
    """
    f.seek(0)
    data = f.read()
    if data[:10] != b'RENPY RPC2':
        raise ValueError("头部不正确")

    position = 10
    slots = {}

    while position + 12 <= len(data):
        slotid, start, length = struct.unpack("<III", data[position:position + 12])
        if (slotid, start, length) == (0, 0, 0):
            break

        if start + length >= len(data):
            raise ValueError("损坏的槽位条目")

        slots[slotid] = (start, length)
        position += 12
    else:
        raise ValueError("损坏的槽位头部结构")

    if slot not in slots:
        raise ValueError("未知的槽位id")

    start, length = slots[slot]
    return data[start:start + length]

@extractor
def extract_slot_legacy(f, slot):
    """
    用于旧版格式的槽位提取器
    """
    if slot != 1:
        raise ValueError("旧版格式仅支持1个槽位")

    f.seek(0)
    data = f.read()

    try:
        data = zlib.decompress(data)
    except zlib.error:
        raise ValueError("旧版格式不包含zlib blob")

    return data

@extractor
def extract_slot_headerscan(f, slot):
    """
    用于更改了魔法数字从而移动头部的情况的槽位提取器。
    """
    f.seek(0)
    data = f.read()

    position = 0
    while position + 36 < len(data):
        a, b, c, d, e, f, g, h, i = struct.unpack("<IIIIIIIII", data[position:position + 36])
        if a == 1 and d == 2 and g == 0 and b + c == e:
            break
        position += 1

    else:
        raise ValueError("找不到头部")

    slots = {}
    while position + 12 <= len(data):
        slotid, start, length = struct.unpack("<III", data[position:position + 12])
        if (slotid, start, length) == (0, 0, 0):
            break

        if start + length >= len(data):
            raise ValueError("损坏的槽位条目")

        slots[slotid] = (start, length)
        position += 12
    else:
        raise ValueError("损坏的槽位头部结构")

    if slot not in slots:
        raise ValueError("未知的槽位id")

    start, length = slots[slot]
    return data[start:start + length]

@extractor
def extract_slot_zlibscan(f, slot):
    """
    用于那些搞乱头部结构到不值得处理的程度的情况的槽位提取器，
    我们直接寻找有效的zlib块。
    """
    f.seek(0)
    data = f.read()

    start_positions = []

    for i in range(len(data) - 1):
        if data[i] != 0x78:
            continue

        if (data[i] * 256 + data[i + 1]) % 31 != 0:
            continue

        start_positions.append(i)

    chunks = []
    for position in start_positions:
        try:
            chunk = zlib.decompress(data[position:])
        except zlib.error:
            continue
        chunks.append(chunk)

    if slot > len(chunks):
        raise ValueError("Zlibscan未找到足够的块")

    return chunks[slot - 1]


@decryptor
def decrypt_zlib(data, count):
    try:
        return zlib.decompress(data)
    except zlib.error:
        return None

@decryptor
def decrypt_hex(data, count):
    if not all(i in b"abcdefABCDEF0123456789" for i in count.keys()):
        return None
    try:
        return data.decode("hex")
    except Exception:
        return None

@decryptor
def decrypt_base64(data, count):
    if not all(i in b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/=\n"
               for i in count.keys()):
        return None
    try:
        return base64.b64decode(data)
    except Exception:
        return None

@decryptor
def decrypt_string_escape(data, count):
    if not all(i >= 0x20 and i < 0x80 for i in count.keys()):
        return None
    try:
        newdata = data.decode("unicode-escape").encode('latin1')
    except Exception:
        return None
    if newdata == data:
        return None
    return newdata


def assert_is_normal_rpyc(f):
    """
    分析单个rpyc文件对象的结构正确性。
    实际上并不对该部分的_内容_做任何说明，只是说我们能够
    从那里切片出来。

    如果成功，返回第一个存储槽的未压缩内容。
    """

    f.seek(0)
    header = f.read(1024)
    f.seek(0)

    if header[:10] != b'RENPY RPC2':
        # 要么是旧版，要么有人搞乱了头部

        # 假设是旧版，看看这个东西是否是有效的zlib blob
        raw_data = f.read()
        f.seek(0)

        try:
            uncompressed = zlib.decompress(raw_data)
        except zlib.error:
            raise ValueError(
                "未找到RENPY RPC2头部，但作为旧版文件的解释失败")

        return uncompressed

    else:
        if len(header) < 46:
            # 10字节头部 + 4 * 9字节内容表
            return ValueError("文件太短")

        a, b, c, d, e, f, g, h, i = struct.unpack("<IIIIIIIII", header[10:46])

        # 头部格式是否匹配默认的ren'py生成文件？
        if not (a == 1 and b == 46 and d == 2 and (g, h, i) == (0, 0, 0) and b + c == e):
            return ValueError("头部数据异常，格式是否增加了额外字段？")

        f.seek(b)
        raw_data = f.read(c)
        f.seek(0)
        if len(raw_data) != c:
            return ValueError("头部数据与文件长度不兼容")

        try:
            uncompressed = zlib.decompress(raw_data)
        except zlib.error:
            return ValueError("槽位1不包含zlib blob")

        if not uncompressed.endswith("."):
            return ValueError("槽位1不包含简单的pickle")

        return uncompressed


def read_ast(f, context):
    diagnosis = ["正在尝试去混淆文件:"]

    raw_datas = set()

    for extractor in EXTRACTORS:
        try:
            data = extractor(f, 1)
        except ValueError as e:
            # 在f-string大括号内，在py3.12之前不允许使用"\"，所以我们使用chr()直到
            # 这是我们的最小py版本
            diagnosis.append(f'策略 {extractor.__name__} 失败: {chr(10).join(e.args)}')
        else:
            diagnosis.append(f'策略 {extractor.__name__} 成功')
            raw_datas.add(data)

    if not raw_datas:
        diagnosis.append("所有策略都失败了。无法提取数据")
        raise ValueError("\n".join(diagnosis))

    if len(raw_datas) != 1:
        diagnosis.append("策略产生了不同的结果。尝试所有选项")

    data = None
    for raw_data in raw_datas:
        try:
            data, stmts, d = try_decrypt_section(raw_data)
        except ValueError as e:
            diagnosis.append(e.message)
        else:
            diagnosis.extend(d)
            context.log("\n".join(diagnosis))
            return stmts

    diagnosis.append("所有策略都失败了。无法去混淆数据")
    raise ValueError("\n".join(diagnosis))


def try_decrypt_section(raw_data):
    diagnosis = []

    layers = 0
    while layers < 10:
        # 我们能加载它了吗？
        try:
            data, stmts = pickle_safe_loads(raw_data)
        except Exception:
            pass
        else:
            return data, stmts, diagnosis

        layers += 1
        count = Counter(raw_data)

        for decryptor in DECRYPTORS:
            newdata = decryptor(raw_data, count)
            if newdata is None:
                continue
            else:
                raw_data = newdata
                diagnosis.append(f'执行了一轮 {decryptor.__name__}')
                break
        else:
            break

    diagnosis.append("不知道如何解密数据。")
    raise ValueError("\n".join(diagnosis))
