#!/usr/bin/env python2

from __future__ import print_function
import os
import sys
import json
import time
import zlib
import gnupg
import random
import socket
import pickle as pkl
import GmailWrapper as gw
import multiprocessing as mp

class OrderHandler(gw.GmailClient):
    def __init__(self, gmail_conf, bar_conf):
        """
        Initialize the order handler from a gmail configuration file
        and an order handler configuration file, both in json format.

        bar_conf configuration items:
            magic_word:         the required word in email subjects
            bar_acknowledge:    a word to check from the bar client
            port:               the network port to TCP over
            buffer_size:        the size of the TCP buffer
        """
        gw.GmailClient.__init__(self, gmail_conf)
        with open(bar_conf) as f:
            config = json.loads(f.read())
        self.magic_word = config['magic_word']
        self.bar_acknowledge = config['bar_acknowledge']
        self.port = config['port']
        self.buffer_size = config['buffer_size']
        self.gpg_passwd = config['gpg_passwd']

        # Set up the subjects for automated emails.
        self.drink_subj = {}
        self.drink_subj['menu'] = self.send_name
        self.drink_subj['menu'] += ': Menu (magic word: %s)' % self.magic_word
        self.drink_subj['confirm'] = self.send_name
        self.drink_subj['confirm'] += ': Drink order received! '
        self.drink_subj['confirm'] += '(magic word: %s)' % self.magic_word
        self.drink_subj['deny'] = self.send_name
        self.drink_subj['deny'] += ': We\'re sorry. We cannot complete '
        self.drink_subj['deny'] += 'your order. '
        self.drink_subj['deny'] += '(magic word: %s)' % self.magic_word

        # Object items.
        self.gpg = None
        self.active_tickets = '/tmp/' + self.email_name.split('@')[0] + '.pkl'
        self.bar_sock = None
        self.bar_conn = None
        self.recv_order_proc = None
        self.sock_notif_proc = None

    def cleanup(self):
        if self.recv_order_proc is not None:
            self.recv_order_proc.terminate()
        if self.sock_notif_proc is not None:
            self.sock_notif_proc.terminate()
        if self.notif_proc is not None:
            self.notif_proc.terminate()
        if self.bar_conn is not None:
            self.bar_conn.close()
        if self.bar_sock is not None:
            self.bar_sock.close()

    def create_ticket(self, message):
        """
        Create an order ticket and send it to the bar.
        """
        # Store an order in the open order queue
        ticket_id = str(int(time.time())) + '.'
        ticket_id += str(random.randint(1 << 10, 1 << 20))

        # Save to tickets file
        with open(self.active_tickets, 'rb') as f:
            tickets = pkl.load(f)
        tickets[ticket_id] = message
        with open(self.active_tickets, 'wb') as f:
            pkl.dump(tickets, f)

        # Create a pickle of minimal information to send to the bar
        order = {'id': ticket_id}
        if '<' in message['from'] and '>' in message['from']:
            order['from'] = message['from'].split('<')[0].strip()
        else:
            order['from'] = message['from']
        order['body'] = message['body']
        order_pkl = zlib.compress(pkl.dumps(order, pkl.HIGHEST_PROTOCOL))
        encrypted = self.gpg.encrypt(order_pkl, None, symmetric='AES256',
                passphrase=self.gpg_passwd, armor=False)

        # Connect to the bar and send the order
        self.bar_conn.send(encrypted.data)

    def run_handler(self):
        # Basic setup
        self.gpg = gnupg.GPG()
        with open(self.active_tickets, 'wb') as f:
            pkl.dump({}, f)

        self.gmail_setup()
        print('Gmail robot ready.')
        self.socket_init()
        print('Socket interface ready.')

        self.recv_order_proc = mp.Process(target=self.recv_order)
        self.daemon = True
        self.recv_order_proc.start()

        self.sock_notif()
        print('Closing connection.')
        self.cleanup()

    def parse_message(self, message, threadId=None):
        """
        Parse a message.

        If the sender didn't put a case-insensitive specified word in
        the subject line, send an error reply.

        If the sender asks for a menu, send one.

        If the sender orders a drink, send the order to the bar.
        """
        subject = message['subject'].lower()
        if self.magic_word not in subject:
            self.reply_nopasswd(message['from'], message['subject'], threadId)
            return

        message['body'] = filter_message_thread(message['body'])
        if 'menu' in message['body'].lower():
            self.reply_menu(message['from'], threadId)
        else:
            message['threadId'] = threadId
            self.create_ticket(message)
        print('Sent reply.')

    def reply_deny(self, ticket_id, reason):
        """
        Reply when the drink cannot be completed.
        """
        with open(self.active_tickets, 'rb') as f:
            tickets = pkl.load(f)
        sender = tickets[ticket_id]['from']
        drink = tickets[ticket_id]['body'].replace('\r\n', '\n').split('\n')
        reply_msg = {}
        reply_msg['to'] = sender
        reply_msg['subject'] = self.drink_subj['deny']
        reply_msg['body'] = '\r\n'.join([
            'We\'re sorry. Unfortunately we cannot complete your order. Please'
            ' see below for more details:',
            '',
            'Order Summary:'])
        for line in drink:
            reply_msg['body'] += '\r\n' + line
        reply_msg['body'] += '\r\n'.join([
            '',
            'Cancellation reason:',
            reason,
            ])
        reply_msg['body'] += '\r\n'.join([
            '', '',
            'If you\'d like to order another drink, please check the menu '
            'message in your inbox for available drink options. If you don\'t '
            'have a drink menu, reply to this message with the word "menu." '
            'We hope you have a wonderful evening!',
            ])
        self.send_message(reply_msg)

    def reply_menu(self, sender, threadId=None):
        """
        Reply to a menu request
        """
        reply_msg = {}
        reply_msg['to'] = sender
        reply_msg['subject'] = self.drink_subj['menu']
        reply_msg['body'] = 'Menu: TBD' # TODO drink menu
        self.send_message(reply_msg, threadId)

    def reply_nopasswd(self, sender, subject, threadId=None):
        """
        Reply when the user didn't put the magic word in the subject.
        """
        reply_msg = {}
        reply_msg['to'] = sender
        reply_msg['subject'] = 'ERROR: Invalid Message Subject: '
        reply_msg['subject'] += subject
        reply_msg['body'] = '\r\n'.join([
            'ERROR: Message subject does not contain the secret word. '
            'Please send another order with the secret word in the subject.',
            '',
            '"Uh uh uh! You didn\'t say the magic word!" - Nedry'])
        self.send_message(reply_msg, threadId)

    def reply_processed(self, ticket_id):
        """
        Reply when the drink is being processed.
        """
        with open(self.active_tickets, 'rb') as f:
            tickets = pkl.load(f)
        sender = tickets[ticket_id]['from']
        drink = tickets[ticket_id]['body'].replace('\r\n', '\n').split('\n')
        reply_msg = {}
        reply_msg['to'] = sender
        reply_msg['subject'] = self.drink_subj['confirm']
        reply_msg['body'] = '\r\n'.join([
            'We have received your order and are preparing your drink! '
            'Your name will appear on the pickup screen near the bar when '
            'your drink is ready.',
            '',
            'Order Summary:'])
        for line in drink:
            reply_msg['body'] += '\r\n' + line
        reply_msg['body'] += '\r\n'.join([
            '',
            'If you\'d like to order another drink, please check the menu '
            'message in your inbox for available drink options. If you don\'t '
            'have a drink menu, reply to this message with the word "menu." '
            'We hope you have a wonderful evening!',
            ])
        self.send_message(reply_msg)

    #---------------------------------------------------------------------------
    # Handler Thread Functions
    def recv_order(self):
        """
        Receive orders from the email robot
        """
        while True:
            new_messages = self.wait_new_messages()
            for message_attr in new_messages:
                # Make something that can be used for analytics.
                print('Received message.')
                message = self.read_message(message_attr)
                self.parse_message(message, message_attr['threadId'])

    def sock_notif(self):
        """
        Get notifications from the bartender software
        """
        while True:
            notif = self.bar_conn.recv(self.buffer_size)
            if not len(notif): # Bartender closed.
                return
            notif = self.gpg.decrypt(notif, passphrase=self.gpg_passwd)
            notif = pkl.loads(zlib.decompress(notif.data))
            status = notif['status']
            if status == 'accepted':
                self.reply_processed(notif['id'])
                continue
            elif status == 'cancelled':
                self.reply_deny(notif['id'], notif['reason'])
            elif status == 'pickup':
                # Pickup just removes the drink from the ticket list.
                pass
            else:
                print('Invalid notification:')
                print(notif)
                continue

            with open(self.active_tickets, 'rb') as f:
                tickets = pkl.load(f)
            tickets.pop(notif['id'], None)
            with open(self.active_tickets, 'wb') as f:
                pkl.dump(tickets, f)

    def socket_init(self):
        """
        Initialize the network communications between the email robot
        and the bar.
        """
        self.bar_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host = '0.0.0.0'
        self.bar_sock.bind((host, self.port))
        self.bar_sock.listen(1)

        # Wait for the bar to connect
        self.bar_conn, addr = self.bar_sock.accept()
        msg = self.bar_conn.recv(self.buffer_size)
        while msg != self.bar_acknowledge:
            self.bar_conn.close()
            self.bar_conn, addr = self.bar_sock.accept()
            msg = self.bar_conn.recv(self.buffer_size)
        self.bar_conn.send(self.bar_acknowledge)
        print('Bar address:', addr[0] + ':' + str(addr[1]))

class OfflineDebug:
    def __init__(self, bar_conf):
        """
        Set up an offline debug simulator
        """
        with open(bar_conf) as f:
            config = json.loads(f.read())
        self.magic_word = config['magic_word']
        self.bar_acknowledge = config['bar_acknowledge']
        self.port = config['port']
        self.buffer_size = config['buffer_size']
        self.gpg_passwd = config['gpg_passwd']
        self.active_tickets = '/tmp/offline-debug-%d.pkl' % self.port

        # Object items.
        self.gpg = None
        self.bar_sock = None
        self.bar_conn = None
        self.recv_order_proc = None
        self.sock_notif_proc = None

    def cleanup(self):
        if self.fake_order_proc is not None:
            self.fake_order_proc.terminate()
        if self.sock_notif_proc is not None:
            self.sock_notif_proc.terminate()
        if self.bar_conn is not None:
            self.bar_conn.close()
        if self.bar_sock is not None:
            self.bar_sock.close()

    def create_ticket(self):
        """
        Create an order ticket and send it to the bar.
        """
        # Store an order in the open order queue
        ticket_id = str(int(time.time())) + '.'
        ticket_id += str(random.randint(1 << 10, 1 << 20))

        # Create a pickle of minimal information to send to the bar
        order = {'id': ticket_id, 'from': 'OfflineDebug:'+str(self.port)}
        order['from'] += '-' + str(random.random())
        order['body'] = ticket_id

        # Save to tickets file
        with open(self.active_tickets, 'rb') as f:
            tickets = pkl.load(f)
        tickets[ticket_id] = order
        with open(self.active_tickets, 'wb') as f:
            pkl.dump(tickets, f)

        order_pkl = zlib.compress(pkl.dumps(order, pkl.HIGHEST_PROTOCOL))
        encrypted = self.gpg.encrypt(order_pkl, None, symmetric='AES256',
                passphrase=self.gpg_passwd, armor=False)

        # Connect to the bar and send the order
        self.bar_conn.send(encrypted.data)
        print('Sent simulated ticket:', ticket_id)

    def fake_order(self):
        while True:
            time.sleep(4)
            #time.sleep(random.randint(10,20))
            self.create_ticket()

    def socket_init(self):
        """
        Initialize the network communications between the email robot
        and the bar.
        """
        self.bar_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host = '127.0.0.1'
        self.bar_sock.bind((host, self.port))
        self.bar_sock.listen(1)

        # Wait for the bar to connect
        self.bar_conn, addr = self.bar_sock.accept()
        msg = self.bar_conn.recv(self.buffer_size)
        while msg != self.bar_acknowledge:
            self.bar_conn.close()
            self.bar_conn, addr = self.bar_sock.accept()
            msg = self.bar_conn.recv(self.buffer_size)
        self.bar_conn.send(self.bar_acknowledge)
        print('Bar address:', addr[0] + ':' + str(addr[1]))

    def run_handler(self):
        self.gpg = gnupg.GPG()
        with open(self.active_tickets, 'wb') as f:
            pkl.dump({}, f)

        print('Waiting for bartender connection.')
        self.socket_init()
        print('Socket interface ready.')

        self.fake_order_proc = mp.Process(target=self.fake_order)
        self.fake_order_proc.daemon = True
        self.fake_order_proc.start()

        self.sock_notif()
        print('Closing connection.')
        self.cleanup()

    def sock_notif(self):
        """
        Get notifications from the bartender software
        """
        while True:
            notif = self.bar_conn.recv(self.buffer_size)
            if not len(notif): # Bartender closed.
                return
            notif = self.gpg.decrypt(notif, passphrase=self.gpg_passwd)
            notif = pkl.loads(zlib.decompress(notif.data))

            print(notif)
            status = notif['status']
            if status == 'accepted':
                continue
            elif status == 'cancelled' or status == 'pickup':
                pass
            else:
                print('Invalid notification:')
                print(notif)
                continue

            with open(self.active_tickets, 'rb') as f:
                tickets = pkl.load(f)
            tickets.pop(notif['id'], None)
            with open(self.active_tickets, 'wb') as f:
                pkl.dump(tickets, f)


def filter_message_thread(msg_body):
    # Select only the most recent message in a thread.
    msg_body = msg_body.replace('\r\n', '\n')
    msg_lines = msg_body.split('\n')
    msg_lines = [l for l in msg_lines if len(l)]
    msg_lines = [l for l in msg_lines if l[0] != '>']

    # Check if "On <stuff> YEAR" is in a line. If it is, assume that's
    # the divider between current message and the old messages
    def check_match(line):
        match = False
        year = time.localtime().tm_year
        if line.find('on '):
            return False

        for y in range(year-1, year+2):
            match += line[3:].find(str(y)) != -1
        return match

    # any lines after the line_idx check fails are previous messages
    line_idx = 0
    while not check_match(msg_lines[line_idx].lower()):
        line_idx += 1
        if line_idx == len(msg_lines):
            break

    # blank out any lines after the message is done
    if line_idx < len(msg_lines):
        for i in range(line_idx, len(msg_lines)):
            msg_lines[i] = ''
    filtered_body = '\r\n'.join(msg_lines)
    return filtered_body

if __name__ == '__main__':
    # Quick test to send an instant reply to a message
    assert len(sys.argv) > 1, 'Need configuration files.'

    # Offline Debug vs Production
    if len(sys.argv) == 2:
        bar_conf = sys.argv[1]
        handler = OfflineDebug(bar_conf)
    else:
        gmail_conf = sys.argv[1]
        bar_conf = sys.argv[2]
        handler = OrderHandler(gmail_conf, bar_conf)

    try:
        handler.run_handler()
    except KeyboardInterrupt:
        print()
        handler.cleanup()
