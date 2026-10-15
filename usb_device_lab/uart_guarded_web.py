"""Compatibility adapter; CLI now delegates to the complete pipeline operator panel."""
import copy
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
    from .uart_pipeline_web import main as serve
    return serve(argv)

if __name__=='__main__':raise SystemExit(main())
