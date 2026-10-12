from __future__ import annotations

from app.runtime.encoding import decode_process_output


def test_decode_process_output_preserves_utf8_chinese():
    assert decode_process_output("中文输出".encode("utf-8")) == "中文输出"


def test_decode_process_output_decodes_windows_gbk_chinese():
    assert decode_process_output("中文输出".encode("gbk")) == "中文输出"


def test_decode_process_output_handles_bom_and_text_values():
    assert decode_process_output(b"\xef\xbb\xbf" + "中文".encode("utf-8")) == "中文"
    assert decode_process_output("已经是文本") == "已经是文本"
    assert decode_process_output(None) == ""


def test_decode_process_output_keeps_utf16_diagnostics():
    assert decode_process_output("command 中文错误".encode("utf-16le")) == "command 中文错误"
    assert decode_process_output("command 中文错误".encode("utf-16")) == "command 中文错误"