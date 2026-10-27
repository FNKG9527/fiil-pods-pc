"""App 协调动作的离线测试：不创建 Tk、不启动 BLE 线程。"""

from __future__ import annotations

from unittest.mock import Mock

from app import App
from fiil_protocol import MafMode


def make_app() -> App:
    app = App.__new__(App)
    app.connected = True
    app.worker = Mock()
    app.status = Mock()
    app.root = Mock()
    app.root.after = lambda delay, callback: None
    app.cinema_on = Mock()
    app.mp_on = Mock()
    app.lifecycle = Mock()
    app.lifecycle.exiting = False
    app.lifecycle.hidden = False
    app.tray = Mock()
    app.tray.available = True
    app.maf_value = None
    app.ll_value = None
    app.cinema_value = None
    app.mp_value = None
    app.tone_value = None
    return app


def test_tray_maf_uses_existing_setter_and_verified_value():
    app = make_app()
    app._on_set_maf = Mock()
    app.run_tray_control("maf", MafMode.WIND)
    app._on_set_maf.assert_called_once_with(MafMode.WIND)


def test_tray_boolean_targets_false_even_when_current_is_true():
    app = make_app()
    app.cinema_on.get.return_value = True
    app._on_set_cinema = Mock()
    app.run_tray_control("cinema", False)
    app.cinema_on.set.assert_called_once_with(False)
    app._on_set_cinema.assert_called_once_with()


def test_tray_tone_and_multipoint_reuse_existing_handlers():
    app = make_app()
    app._on_set_tone = Mock()
    app._on_set_multipoint = Mock()
    app.run_tray_control("tone", 2)
    app.run_tray_control("multipoint", True)
    app._on_set_tone.assert_called_once_with(2)
    app.mp_on.set.assert_called_once_with(True)
    app._on_set_multipoint.assert_called_once_with()


def test_disconnected_tray_action_does_not_submit_ble_command():
    app = make_app()
    app.connected = False
    app._on_set_low_latency = Mock()
    app.run_tray_control("low_latency", 1)
    app._on_set_low_latency.assert_not_called()


def test_window_callbacks_delegate_to_lifecycle_and_close_hides():
    app = make_app()
    app.show_window()
    app.lifecycle.show.assert_called_once_with()
    app.lifecycle.reset_mock()

    app.lifecycle.hide.return_value = True
    app.hide_to_tray()
    app.lifecycle.hide.assert_called_once_with()
    app.lifecycle.exit.assert_not_called()

    app.request_exit()
    app.lifecycle.exit.assert_called_once_with()


def test_close_protocol_is_bound_to_hide_not_exit():
    app = make_app()
    app._install_window_callbacks()
    args = app.root.protocol.call_args
    assert args.args[0] == "WM_DELETE_WINDOW"
    assert args.args[1] == app.hide_to_tray


def test_tray_items_offer_show_only_when_window_hidden():
    app = make_app()
    app.lifecycle.hidden = False
    visible_actions = [item.action for item in app._tray_items()]
    assert "hide" in visible_actions
    assert visible_actions[0] == "show"

    app.lifecycle.hidden = True
    hidden_actions = [item.action for item in app._tray_items()]
    assert hidden_actions[0] == "show"
    assert "hide" not in hidden_actions
