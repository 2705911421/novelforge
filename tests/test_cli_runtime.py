from __future__ import annotations

import importlib


main = importlib.import_module("src.cli.main")


def test_server_banner_falls_back_to_ascii_when_windows_console_rejects_unicode(
    monkeypatch, capsys
):
    def reject_unicode(_panel):
        raise UnicodeEncodeError("gbk", "🚀", 0, 1, "unsupported")

    monkeypatch.setattr(main.console, "print", reject_unicode)

    main._print_server_banner("127.0.0.1", 8787)

    assert "NovelForge Studio starting: http://127.0.0.1:8787" in capsys.readouterr().out
