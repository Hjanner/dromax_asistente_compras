# -*- coding: utf-8 -*-
"""Local FTP server for the demo drugstore offers (see demo_ftp_offers.py).

Serves each drugstore's folder, read-only, to its own user from users.json.
Needs pyftpdlib in a separate venv, never in Odoo's:

    python3 -m venv /opt/odoo17/ftp_demo/venv && /opt/odoo17/ftp_demo/venv/bin/pip install pyftpdlib
    /opt/odoo17/ftp_demo/venv/bin/python scripts/demo_ftp_server.py

Optional env: DEMO_FTP_DIR (default /opt/odoo17/ftp_demo), DEMO_FTP_PORT (2121).
Listens on 127.0.0.1 only.
"""
import json
import os

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

base = os.environ.get('DEMO_FTP_DIR', '/opt/odoo17/ftp_demo')
port = int(os.environ.get('DEMO_FTP_PORT', '2121'))
with open(os.path.join(base, 'users.json')) as fh:
    users = json.load(fh)

authorizer = DummyAuthorizer()
for user, cfg in users.items():
    authorizer.add_user(user, cfg['password'], cfg['home'], perm='elr')

handler = FTPHandler
handler.authorizer = authorizer
handler.passive_ports = range(60000, 60050)
FTPServer(('127.0.0.1', port), handler).serve_forever()
