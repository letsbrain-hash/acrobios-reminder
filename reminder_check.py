# -*- coding: utf-8 -*-
import os
import json
import datetime
import urllib.request
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = '14Oi99MxEF8N1EHN1q93CtgU-0Mb4OUugu8lrwh14KDI'
SHEET_NAME = '自動提醒預約系統'
LINE_TOKEN = os.environ['LINE_CHANNEL_ACCESS_TOKEN']
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def push_message(user_id, text):
    payload = {'to': user_id, 'messages': [{'type': 'text', 'text': text}]}
    req = urllib.request.Request(
        'https://api.line.me/v2/bot/message/push',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {LINE_TOKEN}',
        },
        method='POST',
    )
    urllib.request.urlopen(req)


def main():
    creds = Credentials.from_service_account_file(
        'credentials.json',
        scopes=['https://www.googleapis.com/auth/spreadsheets'],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    rows = ws.get_all_values()
    if len(rows) < 2:
        print('沒有資料列，結束')
        return

    headers = rows[0]
    today = datetime.datetime.now(TAIPEI).date()
    col_status = headers.index('狀態') + 1
    col_sent_at = headers.index('發送時間') + 1

    for i, row in enumerate(rows[1:], start=2):
        data = dict(zip(headers, row))
        name = data.get('客人姓名', '').strip()
        date_str = data.get('回診/預約日期', '').strip()
        days_before = data.get('提前幾天提醒', '').strip()
        staff = data.get('負責服務人員', '').strip()
        user_id = data.get('服務人員userId', '').strip()
        note = data.get('提醒備註', '').strip()
        status = data.get('狀態', '').strip()

        if not name or not date_str or not user_id or status == '已提醒':
            continue

        try:
            appt_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            days = int(days_before) if days_before else 0
        except ValueError:
            print(f'第{i}列日期格式錯誤，略過：{date_str}')
            continue

        remind_date = appt_date - datetime.timedelta(days=days)
        if remind_date > today:
            continue

        text = f'提醒：{name} 預約於 {date_str}（{staff}負責）'
        if note:
            text += f'\n備註：{note}'

        push_message(user_id, text)
        now_str = datetime.datetime.now(TAIPEI).strftime('%Y-%m-%d %H:%M')
        ws.update_cell(i, col_status, '已提醒')
        ws.update_cell(i, col_sent_at, now_str)
        print(f'已發送提醒：{name}（第{i}列）')


if __name__ == '__main__':
    main()
