# OBIWAN: Open Bar Infrastructure With Automated Notifications

This software was written to aid bartenders at the annual University of Toronto
Astronomy (DAA/CITA/Dunlap) holiday party by giving party patrons the ability to
order drinks with their smartphones by sending emails to the bar. This software
uses the Gmail Python API to control a dedicated email address that manages
automated emails and Google Cloud inbox notifications. The bartender has an
ncurses interface where they can easily manage incoming orders and a pickup
queue simultaneously.

## Dependencies

This software requires the Python libraries for the Google API, the Google Cloud
Pub/Sub API, and GnuPG Python library. They can be installed using `pip` with
the command:

```
pip install --upgrade google-api-python-client oauth2client google-cloud-pubsub gnupg
```

## Server configuration

Server-side configuration requires choosing a Gmail account, then setting up
permissions for the Python Gmail API and Google Cloud notifications. The Gmail
Python API [documentation
site](https://developers.google.com/gmail/api/quickstart/python) describes how
to enable the API to access and manage a Gmail account. The server software
needs full Gmail access, so the `SCOPES` variable in the `quickstart.py` script
that Google provides needs to be changed to:

```python
SCOPES = 'https://mail.google.com'
```

After that, `quickstart.py` can be run in the directory that the authentication
credentials json file is located in. The guide to set up the Google Clound
Pub/Sub API (required for inbox notifications) can be found 
[here](https://cloud.google.com/pubsub/docs/quickstart-console).

Configuration information is stored as a `json` file. The Gmail wrapper class
requires the following configuration parameters in the `json` file. All `json`
files that the Gmail configuration refers to must be in the same directory as
the Gmail configuration.

* `token`: Token `json` file created by `quickstart.py`.
* `credentials`: Gmail API credentials `json` file.
* `application`: The service account key `json` file.
* `project_id`: The Project ID generated by Google.
* `topic_name`: The topic for inbox notifications.
* `subscription_name`: The subscription for inbox notifications.
* `email_name`: The email address of the server.
* `send_name`: The name of the email address for sending messages.
* `password`: The Gmail account password for sending via SMTP.

The Order handler class requires the following configuration:

* `magic_word`: The word to put in the email subject.
* `port`: The port to listen on from the bartender client software.
* `buffer_size`: The buffer size of TCP sockets.
* `bar_acknowledge`: A word to check the connection to the bar.
* `gpg_passwd`: Password for GPG symmetric encryption.
* `menu_file`: Text file of the drink menu for automated responses.

Running the server is simple:

    $ python2 OrderHandler.py /path/to/gmail.conf /path/to/orderhandler.conf

This must be done before running the client. Once the bartender connects, a
message will be displayed and the system will be ready to use. It may be
beneficial to put the call to `OrderHander.py` in an infinite loop to keep it
alive indefinitely in case the client ever closes.

## Client configuration

The bartender and pickup windows are ncurses-based interfaces for handling the
email account and displaying ready orders. A limitation of them is that because
they are not intended to be resized, resizing the terminal hasn't been written
into the code (on a TODO list), and trying to resize the terminal will break the
interface. Both the bartender and pickup windows use the same configuration and
it's assumed that the same computer is running both the pickup window and the
bartender interface, but this may differ from the server machine. Here are the
configuration parameters:

* `hostname`: Hostname of the machine running the server
* `ports`: Space-separated string of ports on the server to connect to.
* `buffer_size`: The buffer size of TCP sockets.
* `pickup_port`: Port number for the pickup window.
* `email_name`: The email address of the server.
* `magic_word`: The word to put in the email subject.
* `bar_acknowledge`: A word to check the connection to the bar.
* `gpg_passwd`: Password for GPG symmetric encryption.
* `interval`: Pickup window colour change interval.

In order to run the client software, **first** run the pickup window script:

    $ python2 PickupWindow.py /path/to/bartender.conf

Next, in another terminal, run the bar interface:

    $ python2 BarInterface.py /path/to/bartender.conf

Once that's running, people should be able to email the bar. Quitting the bar
interface (Ctrl-C) quits the pickup window and the server.

## TODO

* Make the ncurses windows handle terminal window resizing.
