from typing import Any

from signal_copier.__main__ import main


def test_main_prints_scaffold_message(capsys: Any) -> None:
    main()
    assert "signal_copier M2 started" in capsys.readouterr().out
