"""窗口尺寸记忆的离线测试。"""

from __future__ import annotations

import json

import ui_state as us


def test_parse_geometry_accepts_tk_formats():
    assert us.parse_geometry("1280x820+120+80") == us.WindowSize(1280, 820)
    assert us.parse_geometry("1280x820-10-20") == us.WindowSize(1280, 820)
    assert us.parse_geometry("1280x820") == us.WindowSize(1280, 820)


def test_parse_geometry_rejects_junk():
    for junk in ("", None, "abc", "1280", "0x820", "x820", "1280xabc", "1280x-5"):
        assert us.parse_geometry(junk) is None, junk


def test_parse_geometry_clamps_too_small_and_too_large():
    assert us.parse_geometry("10x10+0+0") == us.WindowSize(*us.MIN_SIZE)
    assert us.parse_geometry("99999x99999") == us.WindowSize(*us.MAX_SIZE)


def test_missing_file_returns_default():
    from pathlib import Path

    assert us.load(Path("definitely_missing_window.json")) == us.WindowSize()


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "window.json"
    us.save(path, us.WindowSize(1400, 900))
    assert us.load(path) == us.WindowSize(1400, 900)
    assert json.loads(path.read_text(encoding="utf-8")) == {"width": 1400, "height": 900}
    assert not path.with_name("window.json.tmp").exists()


def test_broken_file_falls_back_to_default(tmp_path):
    path = tmp_path / "window.json"
    path.write_text("not json", encoding="utf-8")
    assert us.load(path) == us.WindowSize()


def test_wrong_types_fall_back(tmp_path):
    path = tmp_path / "window.json"
    path.write_text(json.dumps({"width": "big", "height": None}), encoding="utf-8")
    assert us.load(path) == us.WindowSize()


def test_geometry_string_is_valid_for_tk():
    assert us.WindowSize(1280, 820).geometry() == "1280x820"
