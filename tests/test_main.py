from typing import Any

from signal_copier.__main__ import main


def test_main_prints_scaffold_message(capsys: Any) -> None:
    main()
    assert "signal_copier M0 scaffold" in capsys.readouterr().out
