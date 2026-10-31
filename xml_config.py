"""Linear attribute scanning for the vendor's intentionally tolerant XML format."""
import hashlib
from pathlib import Path
import re

MAX_XML_BYTES = 8 * 1024 * 1024


def attributes(text):
    result = {}
    i, size = 0, len(text)
    while i < size:
        if not (text[i].isalnum() or text[i] == '_'):
            i += 1
            continue
        start = i
        while i < size and (text[i].isalnum() or text[i] == '_'):
            i += 1
        name = text[start:i]
        while i < size and text[i].isspace():
            i += 1
        if i >= size or text[i] != '=':
            continue
        i += 1
        while i < size and text[i].isspace():
            i += 1
        if i >= size or text[i] not in ('"', "'"):
            continue
        quote = text[i]
        end = text.find(quote, i + 1)
        if end < 0:
            break
        result[name] = text[i + 1:end]
        i = end + 1
    return result


def network_risks(content):
    tags = [(match.group(1), attributes(match.group(2))) for match in
            re.finditer(r'<(VBAN|VBANStreamOut|VBANStreamIn|VBANCommandIn|VBANDataIn|VBANDataOut)\b([^<>]*)>', content)]
    def enabled(value):
        try:
            return int(value or '0') != 0
        except ValueError:
            return True  # Unknown nonempty state needs review, not silent trust.
    if not any(name == 'VBAN' and enabled(values.get('status')) for name, values in tags):
        return []
    risks = ['此配置会启用 VBAN 网络音频。']
    labels = {'VBANStreamOut': '发送音频', 'VBANStreamIn': '接收音频',
              'VBANCommandIn': '接收网络控制命令', 'VBANDataIn': '接收网络数据/MIDI',
              'VBANDataOut': '发送网络数据/MIDI'}
    for name, values in tags:
        if name in labels and enabled(values.get('status')):
            address = re.sub(r'[\x00-\x1f\x7f]', '?', values.get('ip') or '未限制/未指定')[:160]
            port = re.sub(r'[\x00-\x1f\x7f]', '?', values.get('port') or '默认')[:12]
            risks.append(f"{labels[name]} · 流 {values.get('index', '?')[:12]} · 地址 {address} · 端口 {port}")
    return risks[:32] + ([f'另有 {len(risks) - 32} 项网络配置，请检查原文件。'] if len(risks) > 32 else [])


def inspect_file(path):
    with open(path, 'rb') as stream:
        content = stream.read(MAX_XML_BYTES + 1)
    if len(content) > MAX_XML_BYTES:
        raise ValueError('XML 配置超过 8 MiB。')
    return {'sha256': hashlib.sha256(content).hexdigest(),
            'network_risks': network_risks(content.decode('utf-8-sig'))}
