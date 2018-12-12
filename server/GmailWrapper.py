#!/usr/bin/env python2

################################################################################
## GmailWrapper.py: Get Gmail inbox notifications in real time.
## Copyright (C) 2018   Rachel Domagalski (domagalski@astro.utoronto.ca)
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <https://www.gnu.org/licenses/>.
################################################################################

from __future__ import print_function
import os
import ast
import sys
import glob
import json
import time
import email
import base64
import smtplib
import pickle as pkl
import multiprocessing as mp
from googleapiclient.discovery import build
from httplib2 import Http
from oauth2client import file, client, tools
from google.cloud import pubsub_v1
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

class GmailClient:
    def __init__(self, conf_file):
        # Configuration items
        conf_file = os.path.abspath(conf_file)
        with open(conf_file) as f:
            config = json.loads(f.read())
        conf_dir = os.path.dirname(conf_file)

        self.token = os.path.join(conf_dir, config['token'])
        self.credentials = os.path.join(conf_dir, config['credentials'])
        self.application = os.path.join(conf_dir, config['application'])
        self.project_id = config['project_id']
        self.topic_name = config['topic_name']
        self.subscription_name = config['subscription_name']
        self.email_name = config['email_name']
        self.send_name = config['send_name']
        self.password = config['password']

        self.send_name_email = self.send_name + ' <%s>' % self.email_name
        self.topic_name_full = 'projects/%s/topics/%s' % (self.project_id, self.topic_name)

        self.SCOPES = [
                'https://mail.google.com/',
                'https://www.googleapis.com/auth/gmail.send',
                'https://www.googleapis.com/auth/gmail.modify',
                'https://www.googleapis.com/auth/gmail.compose',
                'https://www.googleapis.com/auth/gmail.metadata'
                ]

        # Object items.
        self.service = None
        self.notif_proc = None
        self.notif_send = None
        self.notif_recv = None
        self.user_hist = None
        self.user_msg = None
        self.hist_id = None

    def changes_new_messages(self, hist_changes):
        # Check if history changes have new messages and return
        # metadata for any new messages detected.
        messages = []
        for ch in [ch for ch in hist_changes if 'messagesAdded' in ch.keys()]:
            for msg in ch['messagesAdded']:
                if self.not_from_self(msg['message']):
                    messages.append(msg['message'])
        return messages

    def gmail_setup(self, only_authorize=False):
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        store = file.Storage(self.token)
        creds = store.get()
        SCOPES = ' '.join(self.SCOPES)
        if not creds or creds.invalid:
            flow = client.flow_from_clientsecrets(self.credentials, SCOPES)
            creds = tools.run_flow(flow, store)

        self.service = build('gmail', 'v1', http=creds.authorize(Http()))
        if only_authorize:
            return
        self.user_labels = self.service.users().labels()
        self.user_hist = self.service.users().history()
        self.user_msg = self.service.users().messages()
        self.watch()

        # Set up the message callback thread
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = self.application
        self.notif_recv, self.notif_send = mp.Pipe(False)
        self.notif_proc = mp.Process(target=self._notification_thread)
        self.notif_proc.daemon = True
        self.notif_proc.start()

    def not_from_self(self, message_attr):
        # check to make sure someone else sent the message received.
        msg_id = message_attr['id']
        message = self.user_msg.get(id=msg_id, userId='me', format='metadata')
        message = message.execute()
        headers = message['payload']['headers']
        msg_from = [h for h in headers if h['name'] == 'From']
        # Assume that if the message has no "from" metadata that
        # someone else sent it
        if len(msg_from):
            msg_from = msg_from[0]['value']
        else:
            msg_from = ''

        email_name = self.email_name
        email_bracket = '<%s>' % self.email_name
        from_self = msg_from == email_name or email_bracket in msg_from
        return not from_self

    def _notification_thread(self):
        project_id = self.project_id
        sub_name = self.subscription_name

        subscriber = pubsub_v1.SubscriberClient()

        # The `subscription_path` method creates a fully qualified identifier
        # in the form `projects/{project_id}/subscriptions/{subscription_name}`
        subscription_path = subscriber.subscription_path(project_id, sub_name)

        def callback(message):
            msg_data = ast.literal_eval(message.data)
            self.notif_send.send(msg_data)
            message.ack()

        # The subscriber is non-blocking. Keep the thread alive always
        subscriber.subscribe(subscription_path, callback=callback)
        while True:
            time.sleep(60)

    def read_message(self, message_attr):
        """
        service: the gmail service created in gmail_setup()
        msg_id: the message id to read
        """
        # TODO handle attachments
        # Read data from a message.
        msg_id = message_attr['id']
        message = self.user_msg.get(id=msg_id, userId='me', format='raw')
        message = message.execute()
        msg_str = base64.urlsafe_b64decode(message['raw'].encode('ASCII'))
        mime_msg = email.message_from_string(msg_str)

        # Parse the message that we care about.
        msg_compact = {}
        msg_compact['from'] = mime_msg['From']
        msg_compact['subject'] = mime_msg['Subject']
        msg_compact['body'] = get_body(mime_msg)

        # Mark as read, then return
        if 'UNREAD' in message_attr['labelIds']:
            mark_read = {'removeLabelIds': ['UNREAD']}
            msg = self.user_msg.modify(userId='me', id=msg_id, body=mark_read)
            msg.execute()
        return msg_compact

    def send_message(self, message, threadId=None):
        """
        Use SMTP to send email. Don't use the Gmail API since that
        can cause conflicts with the threading.
        """
        mime_msg = MIMEText(message['body'])
        mime_msg['To'] = message['to']
        mime_msg['From'] = self.send_name_email
        mime_msg['Subject'] = message['subject']
        msg_string = mime_msg.as_string()

        toaddrs = message['to'].split('<')[-1].split('>')[0]
        username = self.email_name
        password = self.password
        server = smtplib.SMTP('smtp.gmail.com:587')
        server.ehlo()
        server.starttls()
        server.login(username, password)
        server.sendmail(username, toaddrs, msg_string)

    def update_hist(self, msg_data):
        # poll history changes since the last recorded history ID
        # TODO call the watch function after a certain interval.
        hist_id = self.hist_id
        history = self.user_hist.list(userId='me', startHistoryId=hist_id)
        history = history.execute()
        changes = history['history'] if 'history' in history else []
        while 'nextPageToken' in history:
            page_token = history['nextPageToken']
            history = self.user_hist.list(userId='me', startHistoryId=hist_id,
                    pageToken=page_token).execute()
            changes.extend(history['history'])

        hist_id = msg_data['historyId']
        self.hist_id = hist_id
        return changes

    def wait_new_messages(self):
        # we only care if the changes correspond to new messages.
        messages = []
        while not len(messages):
            hist_changes = self.update_hist(self.notif_recv.recv())
            messages = self.changes_new_messages(hist_changes)
        return messages

    def watch(self):
        request = {
            'labelIds': ['INBOX'],
            'topicName': self.topic_name_full
            }
        watcher = self.service.users().watch(userId='me', body=request)
        watcher = watcher.execute()
        self.hist_id = watcher['historyId']
        self.expiration = watcher['expiration']

def get_body(mime_msg):
    # https://stackoverflow.com/questions/17874360/python-how-to-parse-the-body-from-a-raw-email-given-that-raw-email-does-not
    body = ''
    if mime_msg.is_multipart():
        for part in mime_msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get('Content-Disposition'))

            # skip any text/plain (txt) attachments
            if ctype == 'text/plain' and 'attachment' not in cdispo:
                body = part.get_payload(decode=True)  # decode
                break
    # not multipart - i.e. plain text, no attachments, keeping fingers crossed
    else:
        body = mime_msg.get_payload(decode=True)
    body = body.replace('\r\n', '\n')
    return body

if __name__ == '__main__':
    # Quick test to send an instant reply to a message
    assert len(sys.argv) == 2, 'Need configuration file.'
    conf_file = sys.argv[1]

    gmail_client = GmailClient(conf_file)
    gmail_client.gmail_setup()
    new_messages = gmail_client.wait_new_messages()
    for message_attr in new_messages:
        message = gmail_client.read_message(message_attr)

        # reply
        reply_msg = {}
        reply_msg['to'] = message['from']
        reply_msg['subject'] = 'Re: ' + message['subject']
        reply_msg['body'] = 'received message: %s.\r\n' % message['subject']
        gmail_client.send_message(reply_msg, message_attr['threadId'])
