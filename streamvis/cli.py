import argparse
import logging
import os
import pkgutil

from bokeh.application.application import Application
from bokeh.application.handlers import DirectoryHandler, ScriptHandler
from bokeh.server.server import Server

import streamvis as sv

logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """The streamvis command line interface.

    This is a wrapper around bokeh server that provides an interface to launch
    applications bundled with the streamvis package.
    """
    # Discover streamvis apps
    apps_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apps')
    available_apps = []
    for module_info in pkgutil.iter_modules([apps_path]):
        if module_info.ispkg:
            available_apps.append(module_info.name)

    parser = argparse.ArgumentParser(
        prog='streamvis', formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('app', type=str, choices=available_apps, help="streamvis application")

    parser.add_argument(
        '--port', type=int, default=5006, help="a port to listen on for HTTP requests"
    )

    parser.add_argument(
        '--allow-websocket-origin',
        metavar='HOST[:PORT]',
        type=str,
        action='append',
        default=None,
        help="a hostname that can connect to the server websocket",
    )

    parser.add_argument(
        '--page-title', type=str, default="StreamVis", help="browser tab title for the application"
    )

    parser.add_argument(
        '--address',
        metavar='PROTOCOL://HOST:PORT',
        type=str,
        default='tcp://127.0.0.1:9001',
        help="an address string for zmq socket",
    )

    parser.add_argument(
        '--connection-mode',
        type=str,
        choices=['connect', 'bind'],
        default='connect',
        help="whether to bind a socket to an address or connect to a remote socket with an address",
    )

    parser.add_argument(
        '--buffer-size',
        type=int,
        default=1,
        help="a number of last received zmq messages to keep in memory",
    )

    parser.add_argument(
        '--args',
        nargs=argparse.REMAINDER,
        default=[],
        help="command line arguments for the streamvis application",
    )

    args = parser.parse_args()

    sv.page_title = args.page_title
    sv.connection_mode = args.connection_mode
    sv.address = args.address
    sv.buffer_size = args.buffer_size

    app_path = os.path.join(apps_path, args.app)
    logger.info(app_path)

    applications = dict()  # List of bokeh applications

    handler = DirectoryHandler(filename=app_path, argv=args.args)
    applications['/'] = Application(handler)

    statistics_file = os.path.join(app_path, 'statistics.py')
    if os.path.isfile(statistics_file):
        statistics_handler = ScriptHandler(filename=statistics_file)
        applications['/statistics'] = Application(statistics_handler)

    server = Server(
        applications, port=args.port, allow_websocket_origin=args.allow_websocket_origin
    )

    server.start()
    server.io_loop.start()


if __name__ == "__main__":
    main()
