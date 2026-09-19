#!/usr/bin/env python3
"""Loopback-only deterministic Telegram/OpenCode/provider fixtures; no real API calls."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.parse import urlparse, parse_qs


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        data=self.rfile.read(int(self.headers.get('Content-Length',0)))
        try: self.payload=json.loads(data or b'{}')
        except ValueError: self.payload={}
        self.do_GET()

    def do_GET(self):
        q=parse_qs(urlparse(self.path).query)
        path=urlparse(self.path).path
        if path.startswith('/delay/'):
            time.sleep(min(float(path.rsplit('/',1)[1]),10))
        if path.startswith('/status/'):
            status=int(path.rsplit('/',1)[1]);self.send_response(status)
            if status==429:self.send_header('Retry-After','1')
            self.end_headers();return
        if path.startswith('/stream'):
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            try:
                for i in range(3):
                    self.wfile.write(('data: '+json.dumps({'sessionID':q.get('session',['fixture'])[0],'delta':str(i)})+'\n\n').encode());self.wfile.flush()
                    if path=='/stream/abort':break
                    time.sleep(.02)
            except (BrokenPipeError,ConnectionResetError):pass
            self.close_connection=True;return
        body={'ok':True,'result':{'message_id':1,**getattr(self,'payload',{})},
              'sessionID':q.get('session',['fixture'])[0]}
        raw=json.dumps(body).encode();self.send_response(200)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=0);a=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    print(json.dumps({'base_url':f'http://127.0.0.1:{server.server_port}'}),flush=True)
    server.serve_forever()
