# Automated reproduction of issue #20593

[#20593](https://github.com/microsoft/terminal/issues/20593) — after Esc closes
the pane context menu, keyboard focus stays on the dismissed menu item, and Enter
activates it.

Filed from an accessibility pass; no comments; in the Backlog. This directory
holds one reproduction of it, driven through UI Automation. It is not a proposal
to adopt a second test framework — see *Relationship to `WindowsTerminal_UIATests`*
below.

## Running it

```
pip install "wintegrate[video]" pytest
set WT_PORTABLE_DIR=C:\path\to\terminal-1.24.11911.0   # or WINTEGRATE_TERMINAL_EXE=...\wt.exe
pytest src/cascadia/TerminalApp/ui-repro -v -rxX -s
```

The workflow in `.github/workflows/wintegrate-repro-20593.yml` extracts the
portable zip from the v1.24.11911.0 release (SHA-256 and the exe's Authenticode
signature checked) and drops a `.portable` marker beside the exe so settings stay
local. Locally, `WINTEGRATE_TERMINAL_EXE` can point at the installed package's
`wt.exe`.

## What it measures

Windows Terminal 1.24.11911.0 on Windows 11 26100 (arm64) and Windows Server 2025
(x64). The reader is UIA `GetFocusedElement`; the popup count is the number of
visible `Xaml_WindowedPopupClass` windows owned by the Terminal process; the pane
count is the number of `TermControl` elements under the window.

| step | focus | popups | panes |
| --- | --- | --- | --- |
| Menu key | `AppBarButton "Paste"` | 2 | 1 |
| Down ×3 | `AppBarButton "Split pane"` | 2 | 1 |
| Right | `AppBarButton "Duplicate Windows PowerShell"` | 4 | 1 |
| **Esc** | same button, **rect (0, 0, 0, 0)** | 2 (the top level stays open) | 1 |
| Esc ×3 | unchanged | 2 | 1 |
| **Enter** | `TermControl` | 0 | **2** |

Without opening the submenu — Esc with `Split pane` focused — the whole menu
closes (0 popups), focus stays on the `Split pane` button (a 12×12 px rectangle),
and Enter re-opens its submenu at the screen origin, anchored to a button that is
no longer on screen. The report does not mention this second symptom.

The control is the tab header's context menu: right-click the tab, Down to
`Close`, Right into its submenu, Esc, Esc — focus is back on the `TermControl`,
Enter reaches the shell, the pane count stays at 1.

## Why the two menus differ

The tab header's menu is a `MenuFlyout` whose `Closed` handler hands focus back to
the control (`Tab.cpp`, from #5750). The pane menu is the `CommandBarFlyout`
declared in `TermControl.xaml`: its `Closed` handler in `TermControl.cpp` only
restores the original `PrimaryCommands`/`SecondaryCommands`, and
`TerminalPage::_PopulateContextMenu` adds the `Split pane`/`Swap pane` buttons
with nested `CommandBarFlyout`s. Nothing on that path moves focus off the
`AppBarButton` when the flyout closes, so the button — hidden but alive — keeps
keyboard focus and Enter invokes it. Once a submenu was open, further Esc presses
go to that hidden button too, which is why the menu then cannot be dismissed from
the keyboard at all.

#20612 replaces the nested flyouts with `MenuFlyout`s. It may or may not change
this; the top-level `Split pane` button still belongs to the `CommandBarFlyout`.

## How the tests are marked

Three tests assert the *wanted* behaviour and are `xfail(strict=True, raises=...)`:

- **the run is green while the issue reproduces**;
- if the behaviour changes they XPASS and `strict=True` turns the run **red** —
  the signal this directory has done its job and the marker can go;
- `raises` names the one exception each may fail with. Without it, a launch or
  discovery failure inside the fixture is also recorded as XFAIL, and a broken
  fixture reads exactly like the bug being there. That happened once while
  writing this, and only the unmarked control turning red gave it away.

The other three tests are plain: the positive control that a split *is* visible
to the pane count, the tab-menu control, and the version pin.

## Relationship to `WindowsTerminal_UIATests`

The repo already has a UIA-based test project. The two reads this reproduction
depends on — the identity of the focused element after Esc, and the count of
popup windows the process owns — are plain UIA and Win32 and port directly; the
menu is walked by keyboard, so no locator is needed.

## Why `wintegrate`

[`wintegrate`](https://github.com/mangokingTW/wintegrate) is a library written to
make Windows GUI behaviour measurable from CI, where there is no one to watch the
screen. Every step either verifies itself or reports that it could not.

## The recording

`WINTEGRATE_RECORD=1` records the screen from the moment the Terminal window is
up and writes `recording-artifacts/reproduction-20593-<arch>.mp4`. The workflow
sets it and keeps the artifact whatever the outcome. The video is not the
evidence — the assertions are — but it answers *does this look like the bug that
was reported?*
