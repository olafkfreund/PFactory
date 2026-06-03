import http.server
import os
import socketserver

os.chdir(os.path.dirname(os.path.abspath(__file__)))
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()
    def log_message(self, *a): pass
with socketserver.TCPServer(("0.0.0.0", 8300), H) as httpd:
    httpd.serve_forever()
