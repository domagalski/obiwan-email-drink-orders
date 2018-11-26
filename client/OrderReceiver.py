#!/usr/bin/env python2

from __future__ import print_function
import sys
import json
import time
import zlib
import gnupg
import socket
import pickle as pkl
import multiprocessing as mp

class OrderReceiver:
    def __init__(self, conf_file):
        """
        Read in configuration from a json file.
        Configuration items:
            hostname:           hostname of the order server
            ports:              space-separated list of server node ports
            buffer_size:        the size of the TCP buffer
            bar_acknowledge:    a word to check from the bar client
        """
        with open(conf_file) as f:
            config = json.loads(f.read())
        self.hostname = config['hostname']
        self.ports = map(int, config['ports'].split())
        self.buffer_size = config['buffer_size']
        self.bar_acknowledge = config['bar_acknowledge']
        self.gpg_passwd = config['gpg_passwd']

        # Object items
        self.gpg = None
        self.node_procs = []
        self.node_sockets = []
        self.proc_join = None
        self.recv_port = None

    def _get_packet(self, node_idx):
        """
        Get a packet from the server.
        Parse packet into dictionary.
        Send packet to unified receiver.
        Packet is a pickled dictionary.
        """
        sock = self.node_sockets[node_idx]
        while True:
            order = sock.recv(self.buffer_size)
            order = self.gpg.decrypt(order, passphrase=self.gpg_passwd)
            order = pkl.loads(zlib.decompress(order.data))
            order['node'] = node_idx
            self.recv_port.send(order)

    def recv_order(self):
        """
        Receive order from any thread and return it.
        """
        return self.proc_join.recv()

    def socket_init(self):
        """
        Initialize the network connection with the email server.
        """
        self.gpg = gnupg.GPG()
        # Set up the socket receiver threads.
        self.proc_join, self.recv_port = mp.Pipe(False)

        # Send the hello message to the server.
        for i, port in enumerate(self.ports):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((self.hostname, port))
            sock.send(self.bar_acknowledge)
            if sock.recv(self.buffer_size) != self.bar_acknowledge:
                raise ValueError('Invalid acknowledgement.')
            self.node_sockets.append(sock)

            proc = mp.Process(target=self._get_packet, args=(i,))
            proc.daemon = True
            proc.start()
            self.node_procs.append(proc)

if __name__ == '__main__':
    # Quick test of the essential functionality
    assert len(sys.argv) == 2, 'Need configuration file.'
    conf_file = sys.argv[1]

    order_rx = OrderReceiver(conf_file)
    order_rx.socket_init()
    print('Established connection with the order handler server.')
    while True:
        print(order_rx.recv_order())

