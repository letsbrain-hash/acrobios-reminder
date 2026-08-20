# -*- coding: utf-8 -*-
import os
import json
import base64
import hashlib
import datetime
import email.utils
import urllib.request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
DRIVE_FOLDER_ID = '1Mj6kFxeeWP5ZJkryeLXpHcFR-HiJsUfz'
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_TO_USER_ID = os.environ.get('LINE_NOTIFY_USER_ID', '')


def push_line(text):
    if not LINE_TOKEN or not LINE_TO_USER_ID:
        print('[LINE通知略過，缺少設定] ' + text)
        return
    payload = {'to': LINE_TO_USER_ID, 'messages': [{'type': 'text', 'text': text}]}
    req = urllib.request.Request(
        'https://api.line.me/v2/bot/message/push',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_TOKEN}'},
        method='POST',
    )
    urllib.request.urlopen(req)


def get_delegated_gmail(subject_email):
    creds = service_account.Credentials.from_service_account_file(
        'credentials.json', scopes=GMAIL_SCOPES, subject=subject_email
    )
    return build('gmail', 'v1', credentials=creds)


def get_oauth_gmail_and_drive():
    info = json.loads(os.environ['OAUTH_TOKEN_ACROBIOS0503_JSON'])
    creds = UserCredentials.from_authorized_user_info(info)
    return build('gmail', 'v1', credentials=creds), build('drive', 'v3', credentials=creds)


def detect_service_name(subject):
    if 'YouTube' in subject:
        return 'YouTube'
    if 'Google Workspace' in subject:
        return 'GoogleWorkspace'
    if 'Anthropic' in subject or 'receipt' in subject.lower():
        return 'Claude'
    if 'Canva' in subject:
        return 'Canva'
    if 'Google' in subject:
        return 'Google'
    return 'Unknown'


def month_from_date_header(date_str):
    return email.utils.parsedate_to_datetime(date_str).month


def year_month_from_date_header(date_str):
    dt = email.utils.parsedate_to_datetime(date_str)
    return dt.year, dt.month


def find_attachment_parts(part):
    found = []
    if part.get('filename') and part.get('body', {}).get('attachmentId'):
        found.append(part)
    for sub in part.get('parts', []) or []:
        found.extend(find_attachment_parts(sub))
    return found


def recent_range():
    """每天跑，所以抓「最近 45 天」而不是「上個月」。
    45 天保證涵蓋當月＋上個月，發票信一寄到隔天就會被抓進雲端，
    不用再等 5 號／10 號。已存在的檔名 upload_to_drive() 會跳過，重複跑不會出事。"""
    today = datetime.date.today()
    return today - datetime.timedelta(days=45), today + datetime.timedelta(days=1)


def collect_attachments(gmail_service, query):
    """回傳 [(internal_date, year, month, service_name, data)]。
    一封信＝一張發票＝一個檔案，只取第一個附件（Anthropic 一封信夾 Invoice/Receipt
    兩個 PDF，內容相同，取一個就好）。編號在 main() 統一排序後才給，避免抓取順序影響檔名。"""
    start, end = recent_range()
    query = f'{query} after:{start.strftime("%Y/%m/%d")} before:{end.strftime("%Y/%m/%d")}'
    results = gmail_service.users().messages().list(userId='me', q=query, maxResults=20).execute()
    messages = results.get('messages', [])
    items = []
    for m in messages:
        msg = gmail_service.users().messages().get(userId='me', id=m['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in msg['payload']['headers']}
        subject = headers.get('Subject', '')
        year, month = year_month_from_date_header(headers.get('Date', ''))
        service_name = detect_service_name(subject)
        internal_date = int(msg.get('internalDate', 0))
        parts = find_attachment_parts(msg['payload'])
        if not parts:
            continue
        part = parts[0]
        att_id = part['body']['attachmentId']
        att = gmail_service.users().messages().attachments().get(
            userId='me', messageId=m['id'], id=att_id
        ).execute()
        data = base64.urlsafe_b64decode(att['data'])
        items.append((internal_date, year, month, service_name, data))
    return items


def build_items(raw_items):
    """只做排序，不編號。檔名（序號）等到 upload_to_drive() 看過雲端現況才決定。

    ⚠️ 舊做法是在這裡按「本次抓到的信」依序編 01、02…，但抓取視窗改成滾動 45 天之後
    這會出事：例如 8/1、8/7 各一張 Google Workspace 發票，到了 9/20 視窗只剩 8/6 之後，
    8/1 那封掉出視窗，8/7 那封就會變成 01 去覆蓋掉 8/1 的發票（兩張檔案大小還一樣，
    完全看不出來）。所以序號改成依「雲端已經有什麼」來配。"""
    raw_items.sort(key=lambda x: x[0])
    return [(y, m, svc, data) for _, y, m, svc, data in raw_items]


def upload_to_drive(drive_service, items):
    """items: [(year, month, service_name, data)]，已依信件時間排序。

    三條規則，任何情況都不會蓋掉既有檔案：
    1. 內容的 md5 已經在資料夾裡 → 完全跳過（重跑、同一封信被抓兩次都安全）
    2. 內容是新的 → 配給該「年月服務」還沒被佔用的最小序號，建立新檔
    3. 永遠不呼叫 files().update() 覆蓋內容

    要換掉某張發票，請先到雲端把舊檔刪掉／移走，下次跑就會重新抓。
    """
    existing = drive_service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
        fields='files(id, name, md5Checksum)', pageSize=300
    ).execute().get('files', [])
    existing_md5 = {f.get('md5Checksum') for f in existing if f.get('md5Checksum')}
    existing_names = {f['name'] for f in existing}

    from googleapiclient.http import MediaInMemoryUpload
    uploaded, skipped = [], []
    for year, month, service_name, data in items:
        digest = hashlib.md5(data).hexdigest()
        if digest in existing_md5:
            skipped.append(f'{year}年{month}月{service_name}（內容雲端已有）')
            continue

        seq = 1
        while f'{year}年{month}月{service_name}發票收據{seq:02d}.pdf' in existing_names:
            seq += 1
        filename = f'{year}年{month}月{service_name}發票收據{seq:02d}.pdf'

        media = MediaInMemoryUpload(data, mimetype='application/pdf')
        drive_service.files().create(
            body={'name': filename, 'parents': [DRIVE_FOLDER_ID]}, media_body=media
        ).execute()
        existing_names.add(filename)
        existing_md5.add(digest)
        uploaded.append(filename)

    if skipped:
        print(f'略過 {len(skipped)} 個（內容相同）：{skipped}')
    return uploaded


def main():
    try:
        gmail_letsbrain = get_delegated_gmail('letsbrain@acrobios.com')
        gmail_design = get_delegated_gmail('design@acrobios.com')
        gmail_0503, drive_0503 = get_oauth_gmail_and_drive()

        raw_items = []
        raw_items += collect_attachments(gmail_letsbrain, 'from:mail.anthropic.com')
        raw_items += collect_attachments(gmail_letsbrain, 'from:payments-noreply@google.com')
        raw_items += collect_attachments(gmail_0503, 'from:payments-noreply@google.com')
        raw_items += collect_attachments(gmail_design, 'from:no-reply@canva.com')

        items = build_items(raw_items)
        uploaded = upload_to_drive(drive_0503, items)
        print(f'完成，共處理 {len(uploaded)} 個檔案：{uploaded}')

    except Exception as e:
        push_line(f'發票自動化腳本執行失敗：{e}')
        raise


if __name__ == '__main__':
    main()
