"""A busy daily port cannot open an unrelated old service or create an empty store."""
from pathlib import Path
import runpy
import socket
from uuid import uuid4
from unittest.mock import patch

import pytest


def test_v55_occupied_port_does_not_open_browser_or_create_home(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    home = root / 'outputs/v5-acceptance' / ('v55-port-collision-' + uuid4().hex)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr('sys.argv', [str(root / 'start.py'), '--home', str(home), '--port', str(port)])
        with patch('webbrowser.open') as opened:
            with pytest.raises(SystemExit, match='did not open or replace'):
                runpy.run_path(str(root / 'start.py'), run_name='__main__')
            opened.assert_not_called()
    assert not home.exists()
