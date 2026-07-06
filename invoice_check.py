# -*- coding: utf-8 -*-
import os
import json
import base64
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
    if 'Google' in subject:
        return 'Google'
    return 'Unknown'


def month_from_date_header(date_str):
    return email.utils.parsedate_to_datetime(date_str).month


def find_attachment_parts(part):
    found = []
    if part.get('filename') and part.get('body', {}).get('attachmentId'):
        found.append(part)
    for sub in part.get('parts', []) or []:
        found.extend(find_attachment_parts(sub))
    return found


def last_month_range():
    today = datetime.date.today()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - datetime.timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return first_of_prev_month, first_of_this_month


def collect_attachments(gmail_service, query):
    start, end = last_month_range()
    query = f'{query} after:{start.strftime("%Y/%m/%d")} before:{end.strftime("%Y/%m/%d")}'
    results = gmail_service.users().messages().list(userId='me', q=query, maxResults=20).execute()
    messages = results.get('messages', [])
    items = []
    for m in messages:
        msg = gmail_service.users().messages().get(userId='me', id=m['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in msg['payload']['headers']}
        subject = headers.get('Subject', '')
        month = month_from_date_header(headers.get('Date', ''))
        service_name = detect_service_name(subject)
        for part in find_attachment_parts(msg['payload']):
            att_id = part['body']['attachmentId']
            att = gmail_service.users().messages().attachments().get(
                userId='me', messageId=m['id'], id=att_id
            ).execute()
            data = base64.urlsafe_b64decode(att['data'])
            filename = f"{month}月{service_name}發票收據.pdf"
            items.append((filename, data))
    return items


def upload_to_drive(drive_service, items):
    existing = drive_service.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
        fields='files(id, name)'
    ).execute().get('files', [])
    existing_names = {f['name']: f['id'] for f in existing}

    from googleapiclient.http import MediaInMemoryUpload
    uploaded = []
    for filename, data in items:
        media = MediaInMemoryUpload(data, mimetype='application/pdf')
        if filename in existing_names:
            drive_service.files().update(fileId=existing_names[filename], media_body=media).execute()
        else:
            drive_service.files().create(
                body={'name': filename, 'parents': [DRIVE_FOLDER_ID]}, media_body=media
            ).execute()
        uploaded.append(filename)
    return uploaded


def main():
    try:
        gmail_letsbrain = get_delegated_gmail('letsbrain@acrobios.com')
        gmail_0503, drive_0503 = get_oauth_gmail_and_drive()

        items = []
        items += collect_attachments(gmail_letsbrain, 'from:mail.anthropic.com')
        items += collect_attachments(gmail_letsbrain, 'from:payments-noreply@google.com')
        items += collect_attachments(gmail_0503, 'from:payments-noreply@google.com')

        uploaded = upload_to_drive(drive_0503, items)
        print(f'完成，共處理 {len(uploaded)} 個檔案：{uploaded}')

    except Exception as e:
        push_line(f'發票自動化腳本執行失敗：{e}')
        raise


if __name__ == '__main__':
    main()
