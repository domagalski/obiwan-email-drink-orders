#!/usr/bin/env python2

from __future__ import print_function
import os
import sys
import time
import curses
import threading as th
import multiprocessing as mp
from OrderReceiver import OrderReceiver

class BarInterface(OrderReceiver):
    def __init__(self, conf_file):
        OrderReceiver.__init__(self, conf_file)

        # Object items
        self.win_selected = 'order'
        self.order_accepted = False
        self.drinks_waiting = []
        self.drinks_pickup = {}
        self._ev_recv = None
        self._ev_getch = None
        self.get_event = None
        self.notif_event = None

    def cleanup(self):
        """
        Shut everything down.
        """
        self.ui_close()
        if self._ev_recv is not None:
            self._ev_recv.terminate()
        if self._ev_getch is not None:
            self._ev_getch.terminate()
        for proc in self.node_procs:
            proc.terminate()

    def bartender_init(self):
        """
        Connect to the bartender and create the curses window.
        """
        self.socket_init()
        self.ui_open()
        self.events_watchdog_init()

    def display_order(self, order=None):
        """
        Display an order on the window.
        """
        col_white = curses.color_pair(7)
        col_white_bold = curses.color_pair(7) | curses.A_BOLD
        self.update_drink_wait_count()

        if order is not None:
            self.drinks_waiting.append(order)
            if len(self.drinks_waiting) > 1:
                self.update_drink_wait_count()
                return

        if order is None and len(self.drinks_waiting):
            if order is None:
                order = self.drinks_waiting[0]
                self.update_drink_wait_count()

        self.order_win.erase()
        self.order_win.bkgdset(' ', curses.color_pair(4))
        self.order_win.border(0)
        self.show_order_win_keys()
        if order is None:
            self.stdscr.refresh()
            self.order_win.refresh()
            return

        patron = order['from']
        drink_request = order['body'].replace('\r\n', '\n').split('\n')
        self.order_win.addstr(2, 3, 'Patron:', col_white_bold)
        self.order_win.addstr(3, 3, patron, col_white)
        self.order_win.addstr(5, 3, 'Drink request:', col_white_bold)
        for i, line in enumerate(drink_request):
            self.order_win.addstr(6+i, 3, line, col_white)
        self.stdscr.refresh()
        self.order_win.refresh()

    def events_watchdog_init(self):
        self.get_event, self.notif_event = mp.Pipe(False)
        # Event processes to monitor
        self._ev_recv = mp.Process(target=self._events_recv_order)
        self._ev_getch = mp.Process(target=self._events_get_char)
        self._ev_recv.daemon = True
        self._ev_getch.daemon = True
        self._ev_recv.start()
        self._ev_getch.start()

    def _events_recv_order(self):
        while True:
            self.notif_event.send(('order', self.recv_order()))

    def _events_get_char(self):
        while True:
            self.notif_event.send(('input', self.stdscr.getch()))

    def keypress_order_win(self, key):
        """
        Keypresses for order window.
        """
        if self.order_accepted:
            if key == ord('c') or key == ord('C'):
                self.order_win_cancel()
            if key == ord('s') or key == ord('S'):
                self.order_win_send_to_pickup()
        else:
            if key == ord('a') or key == ord('a'):
                self.order_win_accept()
            if key == ord('d') or key == ord('D'):
                self.order_win_decline()

    def main(self):
        """
        Run the bartender interface.
        """
        col_white = curses.color_pair(7)
        col_white_bold = curses.color_pair(7) | curses.A_BOLD
        self.update_drink_wait_count()
        while True:
            event = self.get_event.recv()
            if event[0] == 'order':
                self.display_order(event[1])
            else:
                if self.process_keypress(event[1]) == ord('q'):
                    return

    def order_win_accept(self):
        """
        Approve the current drink in the order queue
        """
        if not len(self.drinks_waiting):
            return
        # TODO notify the email server
        self.order_accepted = True
        self.display_order()

    def order_win_cancel(self):
        """
        Cancel an order.
        """
        if not len(self.drinks_waiting):
            return
        # TODO notify the email server
        # TODO make a pop-up confirming the reason for the cancel
        self.order_accepted = False
        self.drinks_waiting.pop(0)
        self.display_order()

    def order_win_decline(self):
        """
        Deny the current drink in the order queue.
        """
        if not len(self.drinks_waiting):
            return
        # TODO notify the email server
        # TODO make a pop-up confirming the reason for the decline
        self.order_accepted = False
        self.drinks_waiting.pop(0)
        self.display_order()

    def order_win_send_to_pickup(self):
        """
        Mark a drink as ready for pickup
        """
        if not len(self.drinks_waiting):
            return
        # Update the pickup screen
        self.order_accepted = False
        self.drinks_waiting.pop(0)
        self.display_order()

    def process_keypress(self, key):
        """
        Handle processing the keypresses
        """
        if self.win_selected == 'order':
            self.keypress_order_win(key)
        else:
            pass
        return key

    def show_order_win_keys(self):
        """
        Show the keys for the order window.
        """
        nrows, _ = self.size
        col_white = curses.color_pair(7)
        col_white_bold = curses.color_pair(7) | curses.A_BOLD

        if self.order_accepted:
            order_keys = ('Keys:', '(c) Cancel\t(s) Send to pickup')
        else:
            order_keys = ('Keys:', '(a) Accept\t(d) Decline')
        nch = len(order_keys[0]) + 1

        self.order_win.addstr(nrows-4-3-2, 3, order_keys[0], col_white_bold)
        self.order_win.addstr(nrows-4-3-2, 3+nch, order_keys[1], col_white)

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

        self.stdscr.bkgdset(' ', curses.color_pair(7))
        self.stdscr.border(0)
        self.stdscr.keypad(1)

        # Create a pad with a border.
        with os.popen('stty size') as tty:
            nrows, ncols = map(int, tty.read().split())
        for i in range(nrows-2):
            self.stdscr.addstr(i+1, 1, (ncols-2) * ' ')
        self.size = (nrows, ncols)

        self.pickup_win = curses.newwin(nrows-4, ncols/2-6, 2, ncols/2+2)
        self.pickup_win.bkgdset(' ', curses.color_pair(7))
        self.pickup_win.border(0)
        self.pickup_win.keypad(1)

        self.count_win = curses.newwin(3, ncols/2-6, 2+nrows-4-3, 4)
        self.count_win.bkgdset(' ', curses.color_pair(7))
        self.count_win.border(0)
        self.count_win.keypad(1)

        self.order_win = curses.newwin(nrows-4-3, ncols/2-6, 2, 4)
        self.display_order()

        self.stdscr.refresh()
        self.order_win.refresh()
        self.pickup_win.refresh()
        self.count_win.refresh()

    def update_drink_wait_count(self):
        """
        Update the count window to the number of drink orders waiting.
        """
        count_str = 'Drink orders waiting: '
        n_drinks = str(max(0, len(self.drinks_waiting)-1))
        col_white = curses.color_pair(7)
        col_white_bold = curses.color_pair(7) | curses.A_BOLD
        nrows, ncols = self.size
        self.count_win.addstr(1, 3, ' '*(ncols/2-10), col_white)
        self.count_win.addstr(1, 3, count_str, col_white_bold)
        self.count_win.addstr(1, 3+len(count_str), n_drinks, col_white_bold)
        self.stdscr.refresh()
        self.count_win.refresh()

# Set up the bartender interface.
if __name__ == '__main__':
    assert len(sys.argv) == 2, 'Need configuration file.'
    conf_file = sys.argv[1]

    # Connect to server.
    bartender = BarInterface(conf_file)
    bartender.bartender_init()

    try:
        bartender.main()
    except KeyboardInterrupt:
        print()
    finally:
        bartender.cleanup()
