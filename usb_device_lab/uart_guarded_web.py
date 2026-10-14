"""Preflight-protected launcher reusing uart_web without rewriting that module."""
import argparse
import copy
import os
import secrets
import signal
import threading


class GuardedSupervisor:
    def __init__(self, supervisor, checker=None):
        self.base, self.checker, self.last_preflight = supervisor, checker, None
        self.lock, self.cancel = threading.RLock(), threading.Event()
        self.checking, self.closing = False, False

    @property
    def config(self):
        return self.base.config

    def status(self):
        with self.lock:
            report, checking = copy.deepcopy(self.last_preflight), self.checking
        result = self.base.status()
        result.update(preflight=report, preflight_pending=checking, preflight_gate='guarded_web_launcher')
        return result

    def start(self, campaign=None):
        from .uart_preflight import inspect
        checker = self.checker if self.checker is not None else inspect
        with self.lock, self.base.lock:
            if self.closing or self.checking or self.base.closing or self.base.thread is not None and self.base.thread.is_alive():
                raise RuntimeError('Campaign/preflight is active or server is closing')
            self.checking = True
            self.cancel.clear()
            self.last_preflight = None
        try:
            try:
                report = checker(self.base.config, list(self.base.seeds))
            except (OSError, ValueError) as error:
                with self.lock:
                    self.last_preflight = dict(metadata_checks_passed=False, hardware_checked=False,
                        runtime_connected=False, inspection_error=str(error))
                raise RuntimeError('Read-only preflight failed; no campaign was started') from error
            with self.lock:
                if type(report) is not dict:
                    self.last_preflight = dict(metadata_checks_passed=False, hardware_checked=False,
                        runtime_connected=False, inspection_error='Invalid preflight report')
                    raise RuntimeError('Invalid preflight report; no campaign was started')
                self.last_preflight = copy.deepcopy(report)
                if report.get('metadata_checks_passed') is not True:
                    raise RuntimeError('Metadata preflight rejected launch; inspect preflight in status')
                if self.closing or self.cancel.is_set():
                    raise RuntimeError('Launch canceled during preflight')
                self.base.start(campaign)
                if self.cancel.is_set():
                    self.base.stop()
        finally:
            with self.lock:
                self.checking = False

    def stop(self):
        self.cancel.set()
        self.base.stop()

    def close(self):
        self.cancel.set()
        with self.lock:
            self.closing = True
        self.base.close()


def main(argv=None):
    from .uart_config import load
    from .uart_web import Server, Supervisor
    parser = argparse.ArgumentParser(description='UART web launcher with mandatory metadata preflight')
    parser.add_argument('--config', required=True)
    parser.add_argument('--seeds', nargs='+', required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args(argv)
    supervisor = GuardedSupervisor(Supervisor(load(args.config), args.seeds))
    token = os.environ.get('USB_DEVICE_LAB_UART_TOKEN') or secrets.token_urlsafe(32)
    server = Server(supervisor, token, args.port)
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print('Guarded UART panel http://127.0.0.1:%d / token: %s' % (server.server_port, token), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
        supervisor.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
