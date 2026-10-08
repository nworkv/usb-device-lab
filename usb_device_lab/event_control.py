"""Persistent campaign panel with cursor-based event polling, not SSE."""
import sqlite3
from urllib.parse import parse_qs, urlsplit
from .control import run_session
from .persistent_control import PersistentCampaignSupervisor
from .control_http import ControlHandler, ControlServer, SCRIPT
from .event_journal import EventJournal

LIVE_SCRIPT = SCRIPT + b"""
let eventCursor = 0, eventBusy = false, eventRows = [];
const liveLabel = document.createElement('label');
const live = document.createElement('input'); live.type = 'checkbox';
liveLabel.append(live, document.createTextNode(' Live events (1s polling)'));
document.body.append(liveLabel);
const eventOutput = document.createElement('pre'); document.body.append(eventOutput);
el('token').addEventListener('input', () => { eventCursor = 0; eventRows = []; });
async function pollEvents() {
  if (!live.checked || eventBusy || !el('token').value) return;
  eventBusy = true;
  try {
    const response = await fetch('/api/campaign/events?after=' + eventCursor + '&limit=100',
      {headers: {'Authorization': 'Bearer ' + el('token').value}});
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 400) eventCursor = 0;
      throw new Error(data.error || 'Event request failed');
    }
    if (data.gap) eventRows = [{gap: true, oldest: data.oldest}];
    eventRows = eventRows.concat(data.events).slice(-100);
    eventCursor = data.next;
    eventOutput.textContent = JSON.stringify(eventRows, null, 2);
  } catch (error) { eventOutput.textContent = String(error); }
  finally { eventBusy = false; }
}
setInterval(pollEvents, 1000);
"""


class EventCampaignSupervisor(PersistentCampaignSupervisor):
    def __init__(self, config, backend=run_session):
        self._events = None
        super().__init__(config, backend)
        try:
            self._events = EventJournal(self._directory / 'supervisor-events.sqlite3')
            self._events.append(self.status())
        except Exception:
            self.close()
            raise

    def _save(self):
        super()._save()
        if self._events is not None:
            try:
                self._events.append(self.status())
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
                self._persistence_error = str(error)[:4096]
                self._status['persistence_error'] = self._persistence_error
                self._stop.set()

    def events(self, after=0, limit=100):
        return self._events.read(after, limit)

    def close(self):
        super().close()
        if self._events is not None:
            self._events.close()


class EventControlHandler(ControlHandler):
    def do_GET(self):
        if self.path == '/control.js':
            if self.guard(authenticated=False):
                self.reply(200, LIVE_SCRIPT, 'text/javascript; charset=utf-8')
            return
        parsed = urlsplit(self.path)
        if parsed.path != '/api/campaign/events':
            return super().do_GET()
        if not self.guard():
            return
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            if set(query) - {'after', 'limit'} or any(len(values) != 1 for values in query.values()):
                raise ValueError('Invalid query parameters')
            after, limit = query.get('after', ['0'])[0], query.get('limit', ['100'])[0]
            if not after.isascii() or not after.isdigit() or not limit.isascii() or not limit.isdigit():
                raise ValueError('Numeric cursor and limit required')
            data = self.server.supervisor.events(int(after), int(limit))
        except ValueError:
            self.reply(400, {'error': 'Invalid event cursor or limit'})
            return
        except RuntimeError:
            self.reply(503, {'error': 'Event journal unavailable'})
            return
        except sqlite3.Error:
            self.reply(500, {'error': 'Event journal read failed'})
            return
        self.reply(200, data)


class EventControlServer(ControlServer):
    def __init__(self, supervisor, token, port=8765):
        super().__init__(supervisor, token, port)
        self.RequestHandlerClass = EventControlHandler


def main(argv=None):
    import argparse
    import os
    import secrets
    import signal
    from .lab import load
    parser = argparse.ArgumentParser(description='Persistent campaign panel with live event journal')
    parser.add_argument('--config', default='lab.toml')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    token = os.environ.get('USB_DEVICE_LAB_CONTROL_TOKEN') or secrets.token_urlsafe(32)
    with EventCampaignSupervisor(load(args.config)) as supervisor:
        server = EventControlServer(supervisor, token, args.port)
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
