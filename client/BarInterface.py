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
    def __init__(self, conf_file, dbg_no_curses=False):
        OrderReceiver.__init__(self, conf_file)
        self.dbg_no_curses = dbg_no_curses

        # Object items
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

    def check_events(self):
        """
        Check for possible interface updates, such as getting an order
        or the bartender pressing a key on the keyboard.
        """
        return get_event.recv()

    def events_watchdog_init(self):
        self.get_event, self.notif_event = mp.Pipe(False)
        # Create events to watch for
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
            if self.dbg_no_curses:
                time.sleep(60)
            else:
                self.notif_event.send(('input', self.stdscr.getch()))

    def main(self):
        """
        Run the bartender interface.
        """
        col_white = curses.color_pair(7)
        col_white_bold = curses.color_pair(7) | curses.A_BOLD
        while True:
            event = self.get_event.recv()
            if self.dbg_no_curses:
                continue

            if event[0] == 'order':
                order = event[1]
                self.drinks_waiting.append(order)
                patron = order['from']
                drink_request = order['body'].replace('\r\n', '\n').split('\n')

                self.order_win.erase()
                self.order_win.bkgdset(' ', curses.color_pair(4))
                self.order_win.border(0)
                self.order_win.addstr(2, 3, 'Patron:', col_white_bold)
                self.order_win.addstr(3, 3, patron, col_white)
                self.order_win.addstr(5, 3, 'Drink request:', col_white_bold)
                for i, line in enumerate(drink_request):
                    self.order_win.addstr(6+i, 3, line, col_white)
                self.stdscr.refresh()
                self.order_win.refresh()
            else:
                ch = chr(event[1])
                if ch == 'q':
                    return

    def ui_close(self):
        """
        Exit the user interface.
        """
        if self.dbg_no_curses:
            return
        curses.nocbreak();
        self.stdscr.keypad(0);
        curses.echo()
        curses.endwin()

    def ui_open(self):
        """
        Initialize the ncurses user interface.
        """
        if self.dbg_no_curses:
            return
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

        # Create a pad with a border.
        with os.popen('stty size') as tty:
            nrows, ncols = map(int, tty.read().split())
        for i in range(nrows-2):
            self.stdscr.addstr(i+1, 1, (ncols-2) * ' ')
        self.size = (nrows, ncols)
        self.order_win = curses.newwin(nrows-4, ncols/2-6, 2, 4)
        self.order_win.bkgdset(' ', curses.color_pair(4))
        self.order_win.border(0)
        self.order_win.keypad(1)
        self.pickup_q = curses.newwin(nrows-4, ncols/2-6, 2, ncols/2+2)
        self.pickup_q.bkgdset(' ', curses.color_pair(7))
        self.pickup_q.border(0)
        self.pickup_q.keypad(1)

        self.stdscr.refresh()
        self.order_win.refresh()
        self.pickup_q.refresh()

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
