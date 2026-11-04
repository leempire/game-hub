"""Run legacy gameplay browser cases against current code with a temporary backend.

Usage: python test/run_e2e_isolated.py e2e_web_game e2e_uno_smoke
Uses the existing web server; never restarts deployed services. Stop at first failure.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parent.parent


def serve(port, database):
    sys.path.insert(0, str(ROOT / 'server'))
    from app.cfg import config
    import uvicorn
    config.update(db_path=database, admin={}, jwt_secret='isolated-release-browser-key-at-least-32-bytes')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def run_case(name, api, database):
    from playwright.sync_api import Browser
    original = Browser.new_context

    def context(browser, *args, **kwargs):
        result = original(browser, *args, **kwargs)
        result.route('**/api/**', lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                     ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
        result.add_init_script('''const OriginalSocket = window.WebSocket;
          window.WebSocket = class extends OriginalSocket { constructor(url, protocols) {
            const parsed = new URL(url);
            super(parsed.pathname.startsWith('/ws/') ? %s + parsed.pathname + parsed.search : url, protocols);
          }};''' % json.dumps(api.replace('http://', 'ws://')))
        return result

    Browser.new_context = context
    os.environ['GAMEHUB_TEST_API'] = api
    path = ROOT / 'test' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in ('API', 'WS', 'WS_BASE'):
        if hasattr(module, key):
            setattr(module, key, api if key == 'API' else api.replace('http://', 'ws://'))
    if hasattr(module, 'DB'):
        if not database:
            raise ValueError('this case requires the temporary database path')
        module.DB = Path(database)
    return module.main() or 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='*')
    parser.add_argument('--serve', nargs=2, metavar=('PORT', 'DATABASE'))
    parser.add_argument('--case')
    parser.add_argument('--api')
    parser.add_argument('--database')
    args = parser.parse_args()
    if args.serve:
        serve(int(args.serve[0]), args.serve[1])
        return 0
    if args.case:
        return run_case(args.case, args.api, args.database)
    if not args.cases:
        parser.error('specify at least one e2e case')
    for name in args.cases:
        if not name.startswith('e2e_') or '/' in name or not (ROOT / 'test' / f'{name}.py').is_file():
            parser.error(f'invalid case: {name}')
        if name == 'e2e_hot_restart':
            parser.error('hot_restart manages real services and cannot run in this sandbox')
    with tempfile.TemporaryDirectory(prefix='gamehub-release-e2e-') as directory:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        api = f'http://127.0.0.1:{port}'
        output = ROOT / 'tmp/log'
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'release-isolated-backend.log').open('w') as log:
            process = subprocess.Popen([sys.executable, __file__, '--serve', str(port),
                                        str(Path(directory) / 'gamehub.db')], stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('isolated backend exited; see release-isolated-backend.log')
                    try:
                        if requests.get(api + '/api/app/version', timeout=0.3).ok:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError('isolated backend startup timed out')
                for name in args.cases:
                    with (output / f'release-{name}.log').open('w') as case_log:
                        result = subprocess.run([sys.executable, '-u', __file__, '--case', name, '--api', api,
                                                 '--database', str(Path(directory) / 'gamehub.db')],
                                                stdout=case_log, stderr=subprocess.STDOUT)
                    print(f'{name}: {"PASS" if result.returncode == 0 else "FAIL"}', flush=True)
                    if result.returncode:
                        return result.returncode
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
