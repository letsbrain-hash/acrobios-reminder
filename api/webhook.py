# -*- coding: utf-8 -*-
import os
import json
import base64
from http.server import BaseHTTPRequestHandler
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = '14Oi99MxEF8N1EHN1q93CtgU-0Mb4OUugu8lrwh14KDI'
ROSTER_SHEET_NAME = '服務人員名冊'


def get_sheet():
    encoded = os.environ['GOOGLE_CREDENTIALS_BASE64']
    info = json.loads(base64.b64decode(encoded))
    creds = Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(ROSTER_SHEET_NAME)


def upsert_roster(name, user_id):
    ws = get_sheet()
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        if len(row) >= 1 and row[0].strip() == name:
            ws.update_cell(i, 2, user_id)
            return
    ws.append_row([name, user_id])


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            for event in data.get('events', []):
                user_id = event.get('source', {}).get('userId')
                text = event.get('message', {}).get('text', '').strip()
                if user_id and text:
                    upsert_roster(text, user_id)
        except Exception as e:
            print('ERROR:', e)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass
