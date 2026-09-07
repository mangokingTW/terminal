"""Issue #20593 — Esc closes the pane context menu, keyboard focus stays on the
dismissed menu item, and Enter invokes it.

    https://github.com/microsoft/terminal/issues/20593

Filed from an accessibility pass, no comments, in the Backlog. This is a
standalone reproduction driven through UI Automation; the measurement is the
point, not the framework it is written with.

Two menus, same keys, different results (1.24.11911.0, Windows 11 26100 and
Windows Server 2025, x64 and arm64):

    menu                          focus after Esc                          then Enter
    pane menu, submenu open       AppBarButton "Duplicate <profile>",      splits the pane
                                  rect (0, 0, 0, 0)
    pane menu, top level          AppBarButton "Split pane", 12x12 px      re-opens the submenu
                                                                           at the screen origin
    tab header menu (control)     TermControl                              reaches the shell

The pane menu is the `CommandBarFlyout` from `TermControl.xaml`; its `Closed`
handler in `TermControl.cpp` restores the original command lists and nothing
moves focus off the `AppBarButton`. The tab header's menu is a `MenuFlyout`
whose `Closed` handler hands focus back to the control (#5750, `Tab.cpp`).
Once a submenu was open, further Esc presses go to the hidden button too, so the
menu cannot be dismissed from the keyboard at all.

Run it with::

    pip install "wintegrate[video]" pytest
    pytest src/cascadia/TerminalApp/ui-repro -v -rxX -s

The reproductions assert the *wanted* behaviour and are `xfail(strict=True,
raises=...)`. Green while the issue reproduces; an XPASS turns the run red, which
is the signal to drop the marker and keep the test. `raises` names the one
exception each reproduction may fail with, so a launch or discovery failure is a
red run rather than another XFAIL that reads like the bug being there.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("wintegrate", reason="pip install wintegrate")

from wintegrate import Mouse, UiaElement, Window, WindowCensus  # noqa: E402
from wintegrate.interop import (  # noqa: E402
    PROCESS_QUERY_LIMITED_INFORMATION,
    kernel32,
    send_keys,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives a live Windows Terminal")

# The portable zip from the release page, extracted by the workflow with a
# `.portable` marker beside the exe so settings stay in that directory. Locally,
# WINTEGRATE_TERMINAL_EXE can point at any wt.exe, the installed package included.
VERSION = "1.24.11911.0"
FILE_VERSION = "1.24.2607.10001"  # what WindowsTerminal.exe in that package reports
PORTABLE_DIR = Path(os.environ.get("WT_PORTABLE_DIR", rf"C:\wt\terminal-{VERSION}"))
PROCESS = "WindowsTerminal.exe"
WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
POPUP_CLASS = "Xaml_WindowedPopupClass"
UIA_TAB_ITEM = 50019


class FocusStayedOnDismissedItem(AssertionError):
    """Esc closed the flyout and left keyboard focus on one of its buttons."""


class DismissedItemWasInvoked(AssertionError):
    """Enter after Esc ran the menu item that was no longer on screen."""


def reproduces(exc: type[BaseException]):
    return pytest.mark.xfail(
        strict=True,
        raises=exc,
        reason="#20593 is open: the pane context menu leaves focus on the dismissed item",
    )


def settled(read: Callable[[], Any], matches: Callable[[Any], bool], timeout: float = 3.0) -> Any:
    """Polls `read()` until `matches(value)`; returns the last value either way, so the
    caller's own assert produces the diff."""
    deadline = time.monotonic() + timeout
    value = read()
    while not matches(value) and time.monotonic() < deadline:
        time.sleep(0.05)
        value = read()
    return value


def _wt_exe() -> Path:
    """The launcher: wt.exe where the distribution ships one, else WindowsTerminal.exe
    itself, which takes the same command line (a Dev build's zip carries no wt.exe)."""
    override = os.environ.get("WINTEGRATE_TERMINAL_EXE")
    candidates = [Path(override)] if override else [PORTABLE_DIR / "wt.exe", PORTABLE_DIR / PROCESS]
    for exe in candidates:
        if exe.exists():
            return exe
    pytest.fail(
        f"Windows Terminal is not at any of {[str(c) for c in candidates]}. The workflow "
        "extracts the portable zip to WT_PORTABLE_DIR; locally, set WINTEGRATE_TERMINAL_EXE."
    )


def _image_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _focused() -> UiaElement:
    return UiaElement.get_focused()


def _is_terminal(element: UiaElement) -> bool:
    return element.class_name == "TermControl"


def _panes(win: Window) -> list[UiaElement]:
    """Only meaningful while no flyout is open: with a popup up, the window's UIA
    tree reports no TermControl at all."""
    return UiaElement.from_handle(win.hwnd).find_all(class_name="TermControl")


def _terminal_windows() -> list:
    return [w for w in WindowCensus.capture() if w.class_name == WINDOW_CLASS]


def _popups(win: Window) -> list:
    return [
        w
        for w in WindowCensus.capture()
        if w.is_visible and w.pid == win.pid and w.class_name == POPUP_CLASS
    ]


def _focus_settles(matches, timeout: float = 3.0) -> UiaElement:
    return settled(_focused, matches, timeout=timeout)


def _walk_down_until(matches, steps: int = 14) -> UiaElement:
    """Down-arrows through the open menu until the focused item satisfies `matches`.
    Returns the focused element either way; the caller asserts."""
    focused = _focused()
    for _ in range(steps):
        if matches(focused):
            return focused
        before = focused.name
        send_keys("{DOWN}")
        focused = _focus_settles(lambda e, b=before: e.name != b, timeout=2.0)
    return focused


# Menu items are found by what they can do, not by what they say: the same
# build shows "Split pane" on an en-US runner and "拆分窗格" on a zh system, and
# the only top-level pane-menu button that expands is the Split pane one.
def _expands(element: UiaElement) -> bool:
    return "ExpandCollapse" in element.supported_patterns()


def _is_split_pane_entry(element: UiaElement) -> bool:
    return element.class_name == "AppBarButton" and _expands(element)


def _is_tab_submenu_entry(element: UiaElement) -> bool:
    return element.class_name == "MenuFlyoutSubItem"


# Only processes this module launched are ever killed. On windows-latest the
# runner's own console lives inside a Windows Terminal window, so sweeping
# WindowsTerminal.exe by name cancels the job.
_LAUNCHED: set[int] = set()


def _kill_launched() -> None:
    alive = {w.pid for w in _terminal_windows() if w.pid in _LAUNCHED}
    for pid in alive:
        subprocess.run(["taskkill", "/f", "/pid", str(pid)], capture_output=True, check=False)
    left = settled(
        lambda: [w for w in _terminal_windows() if w.pid in alive], lambda ws: not ws, timeout=10.0
    )
    assert not left, f"Terminal windows we launched survived the sweep: {left}"


@pytest.fixture
def terminal(recording):
    """A fresh Terminal window per test: every scenario here changes menu state."""
    exe = _wt_exe()
    _kill_launched()
    others = {w.hwnd for w in _terminal_windows()}
    proc, win = Window.launch_and_discover(
        [str(exe), "-w", "new"],
        timeout=90.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        exclude_hwnds=others,
    )
    _LAUNCHED.add(win.pid)
    try:
        # An App Execution Alias (the local override) does not live beside the exe
        # it starts, so the path check is for the portable layout only.
        image = _image_path(win.pid)
        assert os.environ.get("WINTEGRATE_TERMINAL_EXE") or (
            image and Path(image).parent.resolve() == exe.parent.resolve()
        ), f"discovered {win!r}, whose image is {image!r}, not the Terminal under {exe.parent}"
        assert win.set_foreground(timeout=10.0), f"{win!r} never became the foreground window"
        focused = _focus_settles(_is_terminal, timeout=15.0)
        assert _is_terminal(focused), f"focus is on {focused.describe()}, not the terminal"
        assert len(_panes(win)) == 1, "expected exactly one pane in a new window"
        recording.begin()
        yield win
    finally:
        win.close(force=True)
        proc.terminate()
        _kill_launched()


def _open_pane_menu(win: Window) -> UiaElement:
    """The Menu key with the terminal focused; the flyout puts focus on Paste."""
    send_keys("{APPS}")
    focused = _focus_settles(lambda e: e.class_name == "AppBarButton", timeout=5.0)
    assert focused.class_name == "AppBarButton", (
        f"the pane context menu did not take focus; focus is on {focused.describe()}"
    )
    assert _popups(win), "the Menu key opened no popup window"
    return focused


def _open_split_submenu(win: Window) -> UiaElement:
    """Opens the Split pane submenu; returns its first item (Duplicate <profile>)."""
    _open_pane_menu(win)
    entry = _walk_down_until(_is_split_pane_entry)
    assert _is_split_pane_entry(entry), f"never reached the Split pane entry: {entry.describe()}"
    send_keys("{RIGHT}")
    item = _focus_settles(
        lambda e, n=entry.name: e.class_name == "AppBarButton" and e.name != n, timeout=3.0
    )
    assert item.class_name == "AppBarButton" and item.name != entry.name, (
        f"Right did not open the Split pane submenu; focus is on {item.describe()}"
    )
    return item


def _press_esc_and_wait_for_the_flyout(win: Window, opened: int) -> int:
    send_keys("{ESC}")
    return settled(lambda: len(_popups(win)), lambda n: n < opened, timeout=3.0)


def _dismiss_with_esc(win: Window, presses: int = 3) -> int:
    """Esc until no popup is left, at most `presses` times; returns what is left."""
    left = len(_popups(win))
    for _ in range(presses):
        if not left:
            break
        left = _press_esc_and_wait_for_the_flyout(win, left)
    return left


def test_the_measurement_can_see_a_split(terminal):
    """Positive control: Enter on the submenu item, with the menu open, does split.

    Without this, a pane count that stayed at 1 below could mean the probe cannot
    see panes at all, and the reproduction would be about the probe, not the bug.
    """
    _open_split_submenu(terminal)
    send_keys("{ENTER}")
    panes = settled(lambda: len(_panes(terminal)), lambda n: n == 2, timeout=10.0)
    assert panes == 2, f"Enter on 'Duplicate ...' produced {panes} pane(s), expected a split"


@reproduces(FocusStayedOnDismissedItem)
def test_esc_from_the_submenu_hands_focus_back_to_the_terminal(terminal):
    """One Esc closes the submenu and must leave focus on something that is still
    on screen (the Split pane entry, as a MenuFlyout would); a second Esc closes
    the menu and focus must be back on the terminal."""
    item = _open_split_submenu(terminal)
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    focused = _focus_settles(lambda e: e.name != item.name, timeout=3.0)
    if focused.name == item.name or not focused.is_visible():
        raise FocusStayedOnDismissedItem(
            f"after Esc, focus is on {focused.describe()} rect={focused.bounding_rectangle} "
            f"(the item that had it was {item.name!r}); nothing on screen has it"
        )
    left = _dismiss_with_esc(terminal)
    focused = _focus_settles(_is_terminal, timeout=3.0)
    if left or not _is_terminal(focused):
        raise FocusStayedOnDismissedItem(
            f"after closing the menu with Esc, {left} popup(s) remain and focus is on "
            f"{focused.describe()}, not on the terminal"
        )


@reproduces((FocusStayedOnDismissedItem, DismissedItemWasInvoked))
def test_enter_after_esc_reaches_the_shell_not_the_dismissed_item(terminal):
    """Esc until the menu is gone, then Enter: it must reach the shell. On the
    build with the bug, Esc after the submenu cannot close the menu at all (the
    key goes to the hidden button), and Enter splits the pane."""
    _open_split_submenu(terminal)
    left = _dismiss_with_esc(terminal)
    if left:
        raise FocusStayedOnDismissedItem(
            f"{left} popup(s) still open after three Esc presses: the keys went to the "
            "dismissed submenu item"
        )
    send_keys("{ENTER}")
    # Waits for a split to finish rather than for the count to move: the tree reads
    # 0 panes for a moment while a new one is being built.
    panes = settled(lambda: len(_panes(terminal)), lambda n: n == 2, timeout=5.0)
    time.sleep(1.0)  # hold the result for the recording
    if panes != 1 or _popups(terminal):
        raise DismissedItemWasInvoked(
            f"Enter after Esc left {panes} panes and {len(_popups(terminal))} popup(s): the "
            "dismissed item was still the keyboard focus and got invoked"
        )


@reproduces(FocusStayedOnDismissedItem)
def test_esc_from_the_top_level_hands_focus_back_to_the_terminal(terminal):
    """Same defect without the submenu, not in the report: Esc closes the whole menu,
    focus stays on the 'Split pane' button, and Enter re-opens its submenu anchored
    to a button that is no longer on screen."""
    _open_pane_menu(terminal)
    entry = _walk_down_until(_is_split_pane_entry)
    assert _is_split_pane_entry(entry), f"never reached the Split pane entry: {entry.describe()}"
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    assert not _popups(terminal), "Esc at the top level did not close the menu"
    focused = _focus_settles(_is_terminal, timeout=3.0)
    if not _is_terminal(focused):
        send_keys("{ENTER}")
        reopened = settled(lambda: len(_popups(terminal)), lambda n: n > 0, timeout=3.0)
        time.sleep(1.0)  # hold the result for the recording
        raise FocusStayedOnDismissedItem(
            f"after Esc, focus is on {focused.describe()} rect={focused.bounding_rectangle}; "
            f"Enter then opened {reopened} popup(s) from the dismissed menu"
        )


def test_the_tab_menu_hands_focus_back_on_esc(terminal):
    """The control: the tab header's MenuFlyout returns focus on Esc (#5750), so
    Enter afterwards goes to the shell and nothing splits. Its first submenu
    (Move tab) stands in for the pane menu's Split pane."""
    tabs = UiaElement.from_handle(terminal.hwnd).find_all(control_type_id=UIA_TAB_ITEM)
    assert tabs, "no tab item in the window"
    left, top, right, bottom = tabs[0].bounding_rectangle
    Mouse().right_click((left + right) // 2, (top + bottom) // 2)
    focused = _focus_settles(lambda e: e.class_name.startswith("MenuFlyout"), timeout=5.0)
    assert focused.class_name.startswith("MenuFlyout"), (
        f"the tab context menu did not take focus; focus is on {focused.describe()}"
    )
    entry = _walk_down_until(_is_tab_submenu_entry)
    assert _is_tab_submenu_entry(entry), f"never reached a tab submenu entry: {entry.describe()}"
    send_keys("{RIGHT}")
    _focus_settles(lambda e, n=entry.name: e.name != n, timeout=2.0)
    for _ in range(2):  # one Esc per open level: the submenu, then the menu
        opened = len(_popups(terminal))
        _press_esc_and_wait_for_the_flyout(terminal, opened)
    focused = _focus_settles(_is_terminal, timeout=3.0)
    assert _is_terminal(focused), f"after Esc, focus is on {focused.describe()}, not the terminal"
    send_keys("{ENTER}")
    time.sleep(1.0)
    assert len(_panes(terminal)) == 1, "Enter after Esc split the pane from the tab menu"
    assert not _popups(terminal), "Enter after Esc re-opened a menu"


def test_portable_build_is_the_pinned_version():
    """The exe under test is the one the docstring talks about."""
    exe = _wt_exe()
    if os.environ.get("WINTEGRATE_TERMINAL_EXE"):
        pytest.skip("an explicit WINTEGRATE_TERMINAL_EXE is not version-pinned")
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Item '{exe.parent / PROCESS}').VersionInfo.FileVersion",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout.strip()
    assert out == FILE_VERSION, (
        f"{exe.parent / PROCESS} is {out!r}, the reproduction is about package {VERSION} "
        f"(file version {FILE_VERSION})"
    )
