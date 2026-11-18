"""Single local application launcher; no preconfigured business projects or data migration."""
import argparse
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn
from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=ROOT/'data')
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--role', choices=[r.value for r in Role], default='admin')
    parser.add_argument('--config', action='append', type=Path, default=[])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not (ROOT/'app.html').is_file():
        parser.error('app.html missing; run python frontend/v3/build.py')
    workbench = ROOT/'frontend'/'workbench'/'dist'
    if not (workbench/'index.html').is_file():
        parser.error('Workbench build missing; run setup.cmd')
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', args.port))
        except OSError:
            raise SystemExit('Port occupied; this launcher did not open or replace the existing service.') from None
        port = listener.getsockname()[1]
        configs = [ProjectConfig.model_validate_json(p.read_text(encoding='utf-8-sig')) for p in args.config]
        app = create_app(Store(args.home), configs, actor_for_role('local-user', '本地操作员', Role(args.role)),
                         frontend=ROOT/'app.html', workbench=workbench, enforce_formal_sample_binding=True)
        url = f'http://127.0.0.1:{port}'
        print(f'Stability Data Hub {app.version}\nhome={args.home.resolve()}\n{url}/docs', flush=True)
        if not args.no_browser:
            threading.Timer(1, lambda: webbrowser.open(url)).start()
        uvicorn.Server(uvicorn.Config(app, log_level='info')).run(sockets=[listener])


if __name__ == '__main__':
    main()
