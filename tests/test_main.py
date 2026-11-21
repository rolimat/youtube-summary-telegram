import pytest


def test_main_module_is_importable() -> None:
    from youtube_summary.main import main

    assert callable(main)


def test_main_exits_cleanly() -> None:
    from youtube_summary.main import main

    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0


def test_module_entry_point_exits_cleanly() -> None:
    import runpy

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("youtube_summary", run_name="__main__")
    assert exit_info.value.code == 0
