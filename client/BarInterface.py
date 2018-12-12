#!/usr/bin/env python2

################################################################################
## BarInterface.py: Ncurses-based bartender control interface.
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
import sys
import time
import zlib
import curses
import random
import pickle as pkl
import multiprocessing as mp
from OrderReceiver import OrderReceiver

class BarInterface(OrderReceiver):
    def __init__(self, conf_file):
        OrderReceiver.__init__(self, conf_file)

        # Object items
        self.pu_win_rows = 0
        self.cursor_pos = 0
        self.cursor_row = 0
        self.cursor_offset = 0
        self.win_selected = 'order'
        self.order_accepted = False
        self.drinks_waiting = []
        self.drinks_pickup = []
        self.pickup_sock = None
        self.col_white = None
        self.col_selected = None
        self._ev_recv = None
        self._ev_getch = None
        self.get_event = None
        self.notif_event = None

    def cleanup(self):
        """
        Shut everything down.
        """
        if self._ev_recv is not None:
            self._ev_recv.terminate()
        if self._ev_getch is not None:
            self._ev_getch.terminate()
        for proc in self.node_procs:
            proc.terminate()
        self.ui_close()

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
        self.update_drink_wait_count()
        play_sound = False

        if order is not None:
            play_sound = not len(self.drinks_waiting)
            self.drinks_waiting.append(order)
            if len(self.drinks_waiting) > 1:
                self.update_drink_wait_count()
                return

        if order is None and len(self.drinks_waiting):
            if order is None:
                order = self.drinks_waiting[0]
                self.update_drink_wait_count()

        if play_sound:
            os.system('espeak "Attention! Somebody has ordered a drink." &> /dev/null &')

        self.order_win.erase()
        if self.win_selected == 'order':
            self.order_win.bkgdset(' ', self.col_selected)
        else:
            self.order_win.bkgdset(' ', self.col_white)
        self.order_win.border(0)
        self.show_order_win_keys()

        if order is None:
            self.stdscr.refresh()
            self.order_win.refresh()
            return

        patron = order['from']
        drink_request = order['body'].replace('\r\n', '\n').split('\n')
        self.order_win.addstr(2, 3, 'Patron:', self.col_white_bold)
        self.order_win.addstr(3, 3, patron, self.col_white)
        self.order_win.addstr(5, 3, 'Drink request:', self.col_white_bold)
        for i, line in enumerate(drink_request):
            self.order_win.addstr(6+i, 3, line, self.col_white)
        self.stdscr.refresh()
        self.order_win.refresh()

    def display_pickup(self):
        """
        Display the pickup window.
        """
        # Clear the window first
        self.pickup_win.erase()
        if self.win_selected == 'pickup':
            self.order_win.bkgdset(' ', self.col_white)
            self.pickup_win.bkgdset(' ', self.col_selected)
        else:
            self.order_win.bkgdset(' ', self.col_selected)
            self.pickup_win.bkgdset(' ', self.col_white)
        self.pickup_win.border(0)
        self.show_pickup_win_keys()

        self.pickup_win.addstr(2, 3, 'Pickup Queue:', self.col_white_bold)
        for i in range(min(len(self.drinks_pickup), self.pu_win_rows)):
            patron = self.drinks_pickup[i+self.cursor_offset]
            item_name = patron['name'] + ' (%d)' % len(patron['orders'])
            if self.win_selected == 'pickup' and i == self.cursor_pos:
                self.pickup_win.addstr(3+i, 3, item_name, self.col_cursor)
            else:
                self.pickup_win.addstr(3+i, 3, item_name, self.col_white)

        self.stdscr.refresh()
        self.pickup_win.refresh()

    def events_watchdog_init(self):
        """
        Start threads to watch for events like key presses and new
        drink tickets arriving.
        """
        self.get_event, self.notif_event = mp.Pipe(False)
        # Event processes to monitor
        self._ev_recv = mp.Process(target=self._events_recv_order)
        self._ev_getch = mp.Process(target=self._events_get_char)
        self._ev_recv.daemon = True
        self._ev_getch.daemon = True
        self._ev_recv.start()
        self._ev_getch.start()

    def _events_recv_order(self):
        """
        Watch for new orders
        """
        while True:
            self.notif_event.send(('order', self.recv_order()))

    def _events_get_char(self):
        """
        Watch for key presses.
        """
        while True:
            self.notif_event.send(('input', self.stdscr.getch()))
        self.send_notif(order['node'], notif)

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
                self.order_win_cancel()

    def keypress_pickup_win(self, key):
        """
        Keypresses for the pickup window.
        """
        n_drinks = len(self.drinks_pickup) - 1
        self.cursor_row = self.cursor_offset + self.cursor_pos

        # Scrolling down
        if key == ord('j') or key == curses.KEY_DOWN:
            end_row = self.pu_win_rows - 1
            if self.cursor_pos == end_row and self.cursor_row < n_drinks:
                self.cursor_offset += 1
            self.cursor_pos = min(self.cursor_pos+1, end_row, n_drinks)

        # Scrolling up
        if key == ord('k') or key == curses.KEY_UP:
            if self.cursor_pos == 0 and self.cursor_row > 0:
                self.cursor_offset = max(self.cursor_offset-1, 0)
            self.cursor_pos = max(self.cursor_pos-1, 0)

        # Mark the drink as picked up
        if key == ord('p'):
            self.pickup_drink()

        self.display_pickup()

    def main(self):
        """
        Run the bartender interface.
        """
        self.update_drink_wait_count()
        while True:
            event = self.get_event.recv()
            if event[0] == 'order':
                self.display_order(event[1])
            else:
                self.process_keypress(event[1])

    def order_win_accept(self):
        """
        Approve the current drink in the order queue
        """
        if not len(self.drinks_waiting):
            return
        # Update the UI
        self.order_accepted = True
        self.display_order()

        # notify the email server
        order = self.drinks_waiting[0]
        notif = {'id': order['id'], 'status': 'accepted'}
        self.send_notif(order['node'], notif)

    def order_win_cancel(self):
        """
        Cancel an order.
        """
        if not len(self.drinks_waiting):
            return
        # TODO make a pop-up confirming the reason for the cancel
        self.order_accepted = False
        order = self.drinks_waiting.pop(0)
        self.display_order()

        # notify the email server
        notif = {'id': order['id'], 'status': 'cancelled'}
        notif['reason'] = 'unspecified'
        self.send_notif(order['node'], notif)

    def order_win_send_to_pickup(self):
        """
        Mark a drink as ready for pickup
        """
        if not len(self.drinks_waiting):
            return
        # Update the pickup screen
        self.order_accepted = False
        order = self.drinks_waiting.pop(0)
        self.display_order()

        # Add drink to the pickup queue
        new_drink = {'id': order['id'], 'drink': order['body']}
        new_drink['node'] = order['node']
        not_added = True
        for drink in self.drinks_pickup:
            if order['from'] == drink['name']:
                drink['orders'].append(new_drink)
                not_added = False # LOL, double negative

        if not_added:
            drink = {'name':order['from'], 'orders':[new_drink]}
            pickup_id = str(int(time.time())) + '.'
            pickup_id += str(random.randint(1 << 10, 1 << 20))
            drink['id'] = pickup_id
            self.drinks_pickup.append(drink)

            screen_drink = {'id': pickup_id, 'name': drink['name']}
            screen_drink['action'] = 'add'
            self.pickup_sock.send(pkl.dumps(screen_drink))

        self.display_pickup()

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

        # Notify the pickup screen
        screen_drink = {'id': pickup_order['id'], 'action': 'remove'}
        self.pickup_sock.send(pkl.dumps(screen_drink))

    def process_keypress(self, key):
        """
        Handle processing the keypresses
        """
        if self.win_selected == 'order':
            self.keypress_order_win(key)
        else:
            self.keypress_pickup_win(key)

        # Window selection
        if key == curses.KEY_LEFT:
            self.win_selected = 'order'
        elif key == curses.KEY_RIGHT:
            self.win_selected = 'pickup'

        self.display_order()
        self.display_pickup()

    def show_order_win_keys(self):
        """
        Show the keys for the order window.
        """
        nrows, _ = self.size

        if self.order_accepted:
            ord_keys = '(c) Cancel\t(s) Send to pickup'
        else:
            ord_keys = '(a) Accept\t(d) Decline'

        self.order_win.addstr(nrows-4-3-2, 3, 'Keys:', self.col_white_bold)
        self.order_win.addstr(nrows-4-3-2, 3+6, ord_keys, self.col_white)

    def show_pickup_win_keys(self):
        """
        Show the keys for the order window.
        """
        nrows, _ = self.size

        pu_keys = '(j/k) Scroll up/down\t(p) Mark as picked up'
        self.pickup_win.addstr(nrows-4-2, 3, 'Keys:', self.col_white_bold)
        self.pickup_win.addstr(nrows-4-2, 3+6, pu_keys, self.col_white)

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

        self.col_white = curses.color_pair(7)
        self.col_white_bold = curses.color_pair(7) | curses.A_BOLD
        self.col_selected = curses.color_pair(2)
        self.col_cursor  = curses.color_pair(3)
        self.col_cursor |= curses.A_BOLD
        self.col_cursor |= curses.A_REVERSE


        self.stdscr.bkgdset(' ', self.col_white)
        self.stdscr.border(0)
        self.stdscr.keypad(1)

        # Create a pad with a border.
        with os.popen('stty size') as tty:
            nrows, ncols = map(int, tty.read().split())
        for i in range(nrows-2):
            self.stdscr.addstr(i+1, 1, (ncols-2) * ' ')
        self.size = (nrows, ncols)

        self.count_win = curses.newwin(3, ncols/2-6, 2+nrows-4-3, 4)
        self.count_win.bkgdset(' ', self.col_white)
        self.count_win.border(0)
        self.count_win.keypad(1)

        self.order_win = curses.newwin(nrows-4-3, ncols/2-6, 2, 4)
        self.display_order()

        self.pickup_win = curses.newwin(nrows-4, ncols/2-6, 2, ncols/2+2)
        self.pu_win_rows = nrows - 4 - 6
        self.display_pickup()

        self.stdscr.refresh()
        self.order_win.refresh()
        self.pickup_win.refresh()
        self.count_win.refresh()

    def update_drink_wait_count(self):
        """
        Update the count window to the number of drink orders waiting.
        """
        count_str = 'Drink orders waiting: '
        n_dr = str(max(0, len(self.drinks_waiting)-1))
        nrows, ncols = self.size
        self.count_win.addstr(1, 3, ' '*(ncols/2-10), self.col_white)
        self.count_win.addstr(1, 3, count_str, self.col_white_bold)
        self.count_win.addstr(1, 3+len(count_str), n_dr, self.col_white_bold)
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
