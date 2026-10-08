"""Durable supervisor snapshots, not PRNG checkpoints or automatic restart."""
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from .control import CampaignSupervisor, run_session
from .snapshot_schema import decode_snapshot


class PersistentCampaignSupervisor(CampaignSupervisor):
    def __init__(self, config, backend=run_session):
        super().__init__(config, backend)
        self._closed = False
        self._persistence_error = None
        self._directory = Path(config.results_dir)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._state_path = self._directory / 'supervisor-state.json'
        identity = [str(Path(config.path).resolve()), config.host, config.usb_bus, config.udc]
        self._identity = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        self._owner = open(self._directory / 'supervisor.lock', 'a')
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self._state_path.exists():
                self._restore()
        except Exception:
            self._owner.close()
            raise

    def _restore(self):
        with self._state_path.open('rb') as source:
            raw = source.read(65537)
        status, parameters = decode_snapshot(raw, self._identity)
        self._status = status
        self._last = parameters
        if self._status['state'] in {'starting', 'running', 'stopping'}:
            self._status['state'] = 'interrupted'
            self._status['error'] = 'Previous supervisor exited without a terminal snapshot; inspect the lab before resume'

    def _save(self):
        temporary = None
        try:
            data = {'version': 1, 'identity': self._identity, 'status': self._status, 'parameters': self._last}
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            decode_snapshot(encoded, self._identity)
            if len(encoded) > 65536:
                raise ValueError('Supervisor snapshot is too large')
            fd, temporary = tempfile.mkstemp(prefix='.supervisor-', dir=self._directory)
            with os.fdopen(fd, 'wb') as target:
                target.write(encoded)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self._state_path)
            temporary = None
            fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, ValueError) as error:
            self._persistence_error = str(error)[:4096]
            self._status['persistence_error'] = self._persistence_error
            self._stop.set()
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def start(self, **parameters):
        with self._lock:
            if self._closed:
                raise RuntimeError('Supervisor is closed')
            if self._persistence_error:
                raise RuntimeError('Snapshot persistence failed; repair storage and restart supervisor')
            super().start(**parameters)
            self._save()
            return dict(self._status)

    def _publish(self, summary):
        with self._lock:
            super()._publish(summary)
            self._save()

    def _work(self, config):
        super()._work(config)
        with self._lock:
            if self._persistence_error:
                self._status['state'] = 'failed'
                self._status['error'] = 'Snapshot persistence failed'
            self._save()
            if self._persistence_error:
                self._status['state'] = 'failed'
                self._status['error'] = 'Snapshot persistence failed'

    def stop(self):
        with self._lock:
            if not self._closed:
                super().stop()
                self._save()
            return dict(self._status)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            super().stop()
            self._save()
        self.wait()
        self._owner.close()

    def __enter__(self):
        return self

    def __exit__(self, *errors):
        self.close()


def main(argv=None):
    import argparse
    import secrets
    import signal
    from .lab import load
    from .control_http import ControlServer
    parser = argparse.ArgumentParser(description='Persistent local campaign control panel')
    parser.add_argument('--config', default='lab.toml')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    token = os.environ.get('USB_DEVICE_LAB_CONTROL_TOKEN') or secrets.token_urlsafe(32)
    with PersistentCampaignSupervisor(load(args.config)) as supervisor:
        server = ControlServer(supervisor, token, args.port)
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        print('Control panel: ' + server.expected_origin, flush=True)
        print('Local bearer token: ' + token, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            with server.action_lock:
                server.closing = True
                supervisor.stop()
            server.server_close()
            supervisor.close()
            signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
