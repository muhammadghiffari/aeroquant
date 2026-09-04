from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_autorestart_installer_uses_python_executable_path():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "(Get-Command python.exe -ErrorAction Stop).Path" in installer


def test_autorestart_installer_runs_preflight_from_project_root():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "Set-Location -LiteralPath $ProjectRoot" in installer


def test_autorestart_installer_ignores_new_task_instances():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "-MultipleInstances IgnoreNew" in installer


def test_autorestart_installer_does_not_retrigger_long_running_loops():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "-RepetitionInterval" not in installer


def test_autorestart_installer_requires_elevated_shell_for_registration():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "WindowsBuiltInRole]::Administrator" in installer
    assert "Run this script from an elevated PowerShell" in installer


def test_autorestart_installer_verifies_installed_triggers_are_non_repeating():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "Get-ScheduledTask -TaskName" in installer
    assert ".Repetition" in installer
    assert "still has a repeating trigger" in installer


def test_autorestart_installer_stops_existing_instances_before_replacing_tasks():
    installer = (PROJECT_ROOT / "scripts" / "install_autorestart.ps1").read_text()

    assert "Stop-ScheduledTask -TaskName" in installer
    assert "State -eq \"Running\"" in installer


def test_loop_lock_rejects_a_second_entry_loop(monkeypatch, tmp_path):
    import os

    import pytest

    if os.name != "nt":
        pytest.skip("Windows file lock is only used by the scheduled runtime")

    import main

    monkeypatch.setattr(main, "_LOOP_LOCK_PATH", tmp_path / "momentum_loop.lock")
    first = main._acquire_loop_mutex()
    second = main._acquire_loop_mutex()
    try:
        assert first is not None
        assert second is None
    finally:
        main._release_loop_mutex(second)
        main._release_loop_mutex(first)


def test_loop_lock_uses_a_shared_state_path():
    import main

    assert main._LOOP_LOCK_PATH.name == "momentum_loop.lock"
