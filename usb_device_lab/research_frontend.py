"""Designed console mounted on the authenticated research API."""
import os
import secrets
import signal
from urllib.parse import urlsplit
from .research_service import Handler as APIHandler, Server as APIServer, Supervisor
from .research_ui import PAGE, STYLE, SCRIPT

class Handler(APIHandler):
    def do_GET(self):
        path=urlsplit(self.path).path
        assets={'/':(PAGE,'text/html; charset=utf-8'),'/research.js':(SCRIPT,'text/javascript; charset=utf-8'),'/research.css':(STYLE,'text/css; charset=utf-8')}
        if path not in assets:return super().do_GET()
        if not self.guard(auth=False):return
        if self.path!=path:return self.reply(400,{'error':'Параметры статической страницы не допускаются'})
        data,kind=assets[path];return self.reply(200,data,kind)

class Server(APIServer):
    def __init__(self,supervisor,token,port=8766):
        super().__init__(supervisor,token,port);self.RequestHandlerClass=Handler

def serve(config,port=8766):
    supervisor=Supervisor(config);token=os.environ.get('USB_DEVICE_LAB_DUAL_TOKEN') or secrets.token_urlsafe(32)
    server=Server(supervisor,token,port)
    previous=signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('Панель http://127.0.0.1:%d / токен доступа: %s'%(server.server_port,token),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:signal.signal(signal.SIGTERM,previous);server.server_close();supervisor.close()
