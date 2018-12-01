#!/usr/bin/env python2

from __future__ import print_function
import os
import sys
import time
import json
import curses
import socket
import random
import pickle as pkl
import multiprocessing as mp

class PickupWindow:
    def __init__(self, conf_file):
        """
        Initialize the configuration parameters from a conf file.
        """
        with open(conf_file) as f:
            config = json.loads(f.read())
        self.buffer_size = config['buffer_size']
        self.bar_acknowledge = config['bar_acknowledge']
        self.port = config['pickup_port']
        self.interval = config['interval']
        self.magic_word = config['magic_word']
        self.email_name = config['email_name']

        # Object items
        self.win_idx = 0
        self.drinks_pickup = []
        self._ev_recv = None
        self._ev_timer = None
        self.get_event = None
        self.notif_event = None
        self.bar_sock = None

    def cleanup(self):
        """
        Shut everything down.
        """
        if self._ev_recv is not None:
            self._ev_recv.terminate()
        if self._ev_timer is not None:
            self._ev_timer.terminate()
        if self.bar_sock is not None:
            self.bar_sock.close()
        self.ui_close()

    def display_info(self, timer=False):
        """
        Display some information about the bar system.
        """
        nrows, ncols = self.size
        email = self.email_name
        magicword = self.magic_word
        info_str = ' '.join([
            'Hello there and welcome to the Open Bar Infrastructure With',
            'Automated Networking (OBIWAN). To see the drink menu, send a',
            'message to %s. Make sure to have the word' % email,
            '"%s" in the subject of the email to let the mail' % magicword,
            'server know your order is intentional. To order a drink, just',
            'reply to the message with the menu with the drink that you want',
            'and we\'ll make that drink for you! When your drink is ready,',
            'your name will be in the box at the right-hand side of the',
            'screen. Have fun!'
            ]).split()

        if timer:
            n_colour = len(self.colour_list)
            col_idx = int(random.random()*n_colour)
            self.info_border = self.colour_list[col_idx]
        self.info_win.erase()
        self.info_win.bkgdset(' ', self.info_border)
        self.info_win.border(0)

        self.info_win.addstr(2,4, 'Information:', self.col_white_bold)

        pad = 4
        max_chars = 2*ncols/5 - 6 - 2*pad
        line = ''
        idx = 0
        for word in info_str:
            if line == '':
                line = word
            elif len(' '.join([line, word])) < max_chars:
                line = ' '.join([line, word])
            else:
                self.info_win.addstr(4+idx, 4, line, self.col_white)
                line = word
                idx += 1
        self.info_win.addstr(4+idx, 4, line, self.col_white)
        self.stdscr.refresh()
        self.info_win.refresh()

    def display_pickup(self, order=None, timer=False):
        """
        Display the pickup window.
        """
        # Set up the window
        if timer:
            n_colour = len(self.colour_list)
            col_idx = int(random.random()*n_colour)
            self.pu_border = self.colour_list[col_idx]
        self.pickup_win.erase()
        self.pickup_win.bkgdset(' ', self.pu_border)
        self.pickup_win.border(0)

        # Add a string
        self.pickup_win.addstr(2, 4, 'Ready for pickup:', self.col_white_bold)

        # Append a new order to the drink list.
        if order is not None:
            self.process_action(order)

        # Get the index for which to display, if applicable
        nrows, ncols = self.size
        self.curr_len = len(self.drinks_pickup)
        max_drinks = nrows - 4 - 2 - 4
        if self.curr_len > max_drinks and timer:
            self.win_idx += 1
            self.win_idx %= self.curr_len / max_drinks + 1
        elif self.curr_len <= max_drinks:
            self.win_idx = 0
        #else:
        #    self.win_idx = 0

        # Run the display
        idx = self.win_idx
        for i in range(idx*max_drinks, min((idx+1)*max_drinks, self.curr_len)):
            row_idx = i % max_drinks
            name = self.drinks_pickup[i]['name']
            self.pickup_win.addstr(4+row_idx, 4, name, self.col_white_bold)

        self.stdscr.refresh()
        self.pickup_win.refresh()
        self.prev_len = len(self.drinks_pickup)

    def _events_recv_order(self):
        """
        Watch for new orders
        """
        while True:
            order = self.bar_conn.recv(self.buffer_size)
            if not len(order):
                self.notif_event.send(('quit', None))
            order = pkl.loads(order)
            self.notif_event.send(('order', order))

    def _events_timer(self):
        """
        Watch for the timer.
        """
        while True:
            time.sleep(self.interval)
            self.notif_event.send(('timer', None))

    def events_watchdog_init(self):
        """
        Start threads to watch for events like key presses and new
        drink tickets arriving.
        """
        self.get_event, self.notif_event = mp.Pipe(False)
        # Event processes to monitor
        self._ev_recv = mp.Process(target=self._events_recv_order)
        self._ev_timer = mp.Process(target=self._events_timer)
        self._ev_recv.daemon = True
        self._ev_timer.daemon = True
        self._ev_recv.start()
        self._ev_timer.start()

    def main(self):
        """
        Run the bartender interface.
        """
        while True:
            event = self.get_event.recv()
            if event[0] == 'order':
                self.display_pickup(event[1])
            elif event[0] == 'timer':
                self.process_timer()
            else:
                return

    def pickup_drink(self):
        """
        Notify when someone picks up their drinks
        """
        if not len(self.drinks_pickup):
            return

        # Remove the drink from the pickup list and
        pickup_order = self.drinks_pickup.pop(self.cursor_row)
        n_drinks = len(self.drinks_pickup)
        end_row = self.pu_win_rows - 1

        # decrement the cursor offset if at the last drink
        if self.cursor_row == n_drinks:
            self.cursor_offset = max(0, self.cursor_offset-1)
            self.cursor_row = self.cursor_pos + self.cursor_offset

        # Make sure the cursor position and offset stays in list boundaries
        n_drinks = max(0, n_drinks - 1)
        self.cursor_pos = min(self.cursor_pos, self.cursor_row, n_drinks)
        self.cursor_row = self.cursor_pos + self.cursor_offset
        self.cursor_offset = min(self.cursor_offset, n_drinks-end_row)
        self.cursor_offset = max(0, self.cursor_offset)

        # notify the email server
        for order in pickup_order['orders']:
            notif = {'id': order['id'], 'status': 'pickup'}
            self.send_notif(order['node'], notif)

    def pickup_screen_init(self):
        """
        Connect to the bartender and create the curses window.
        """
        self.ui_open()
        self.events_watchdog_init()

    def process_action(self, order):
        """
        Add/Remove drinks from the pickup list.
        """
        if order['action'] == 'add':
            self.drinks_pickup.append(order)
        else:
            idx = 0
            while idx < len(self.drinks_pickup):
                if self.drinks_pickup[idx]['id'] == order['id']:
                    break
                idx += 1
            self.drinks_pickup.pop(idx)

    def process_timer(self):
        """
        Handle timing switches.
        """
        self.display_info(timer=True)
        self.display_pickup(timer=True)

    def socket_init(self):
        """
        Initialize the network communications between the email robot
        and the bar.
        """
        self.bar_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host = '127.0.0.1' # TODO give this the ability to be non-local
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

        self.display_info(timer=True)
        self.display_pickup(timer=True)

    def ui_close(self):
        """
        Exit the user interface.
        """
        curses.nocbreak();
        self.stdscr.keypad(0);
        curses.echo()
        curses.endwin()

    def ui_open(self):
        """
        Initialize the ncurses user interface.
        """
        self.stdscr = curses.initscr()

        curses.curs_set(0)
        curses.cbreak()
        curses.noecho()

        # Set the basic colors.
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_RED,     curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_GREEN,   curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_YELLOW,  curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_BLUE,    curses.COLOR_BLACK)
            curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
            curses.init_pair(6, curses.COLOR_CYAN,    curses.COLOR_BLACK)
            curses.init_pair(7, curses.COLOR_WHITE,   curses.COLOR_BLACK)

        # Get a list of colours
        self.colour_list = []
        for i in range(1,8):
            self.colour_list.append(curses.color_pair(i))

        self.col_white = curses.color_pair(7)
        self.col_white_bold = curses.color_pair(7) | curses.A_BOLD
        self.col_selected = curses.color_pair(2)
        self.col_cursor  = curses.color_pair(3)
        self.col_cursor |= curses.A_BOLD
        self.col_cursor |= curses.A_REVERSE

        self.stdscr.bkgdset(' ', self.col_white)
        self.stdscr.keypad(1)

        # Create a pad with a border.
        with os.popen('stty size') as tty:
            nrows, ncols = map(int, tty.read().split())
        for i in range(nrows-2):
            self.stdscr.addstr(i+1, 1, (ncols-2) * ' ')
        self.size = (nrows, ncols)

        self.info_win = curses.newwin(nrows-4, 2*ncols/5-6, 2, 4)
        self.info_border = self.col_white
        self.display_info()

        self.pickup_win = curses.newwin(nrows-4, 3*ncols/5-6, 2, 2*ncols/5+2)
        self.pu_border=self.col_white
        self.pickup_win.bkgdset(' ', self.col_white)
        self.pickup_win.border(0)
        waiting = 'Waiting for connection...'
        self.pickup_win.addstr(2, 4, waiting, self.col_white)

        self.stdscr.refresh()
        self.info_win.refresh()
        self.pickup_win.refresh()

        # Have a loading screen waiting for the socket.
        self.socket_init()

# Set up the bartender interface.
if __name__ == '__main__':
    assert len(sys.argv) == 2, 'Need configuration file.'
    conf_file = sys.argv[1]

    # Connect to server.
    pickup_screen = PickupWindow(conf_file)
    pickup_screen.pickup_screen_init()

    try:
        pickup_screen.main()
    except KeyboardInterrupt:
        print()
    finally:
        pickup_screen.cleanup()
