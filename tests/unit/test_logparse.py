import base64
import json

from dispatcher.logparse import parse


def line(s, text=b"", **extra):
    d = {"s": s, **extra}
    if text:
        d["b"] = base64.b64encode(text).decode()
    return json.dumps(d)


def test_separates_streams_and_flags():
    raw = "\n".join([line("out", b"hello\n"), line("err", b"oops\n"), line("meta", truncated=True)])
    p = parse(raw)
    assert (p.stdout, p.stderr, p.truncated, p.timeout) == ("hello\n", "oops\n", True, False)


def test_timeout_flag_and_partial_trailing_line_ignored():
    raw = line("out", b"a") + "\n" + line("meta", timeout=True) + '\n{"s":"out","b":"QUJ'
    p = parse(raw)
    assert p.stdout == "a" and p.timeout is True


def test_per_stream_cap_enforced_even_if_wrapper_misbehaves():
    raw = "\n".join(line("out", b"x" * 100) for _ in range(5))
    p = parse(raw, max_bytes=250)
    assert len(p.stdout) == 250 and p.truncated is True


def test_non_utf8_does_not_crash():
    assert parse(line("out", b"\xff\xfe")).stdout
