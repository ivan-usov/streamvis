import argparse
import logging
import os
import pkgutil
from functools import partial
from threading import Thread

from bokeh.application.application import Application
from bokeh.application.handlers import ScriptHandler
from bokeh.server.server import Server

from streamvis import __version__
from streamvis.handler import StreamvisHandler
from streamvis.receiver import Receiver, StatisticsHandler

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """The streamvis command line interface.

    This is a wrapper around bokeh server that provides an interface to launch
    applications bundled with the streamvis package.
    """
    base_path = os.path.dirname(os.path.abspath(__file__))

    # Discover streamvis apps
    apps_path = os.path.join(base_path, "apps")
    available_apps = []
    for module_info in pkgutil.iter_modules([apps_path]):
        available_apps.append(module_info.name)

    # Prepare argument parser
    parser = argparse.ArgumentParser(
        prog="streamvis", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    parser.add_argument("app", type=str, choices=available_apps, help="streamvis application")

    parser.add_argument(
        "--port", type=int, default=5006, help="a port to listen on for HTTP requests"
    )

    parser.add_argument(
        "--allow-websocket-origin",
        metavar="HOST[:PORT]",
        type=str,
        action="append",
        default=None,
        help="a hostname that can connect to the server websocket",
    )

    parser.add_argument(
        "--page-title", type=str, default="StreamVis", help="browser tab title for the application"
    )

    parser.add_argument(
        "--address",
        metavar="PROTOCOL://HOST:PORT",
        type=str,
        default="tcp://127.0.0.1:9001",
        help="an address string for zmq socket",
    )

    parser.add_argument(
        "--connection-mode",
        type=str,
        choices=["connect", "bind"],
        default="connect",
        help="whether to bind a socket to an address or connect to a remote socket with an address",
    )

    parser.add_argument(
        "--buffer-size",
        type=int,
        default=1,
        help="a number of last received zmq messages to keep in memory",
    )

    parser.add_argument(
        "--hit-threshold",
        type=int,
        default=15,
        help="a number of spots above which a shot is registered in statistics as 'hit'",
    )

    parser.add_argument(
        "--args",
        nargs=argparse.REMAINDER,
        default=[],
        help="command line arguments for the streamvis application",
    )

    args = parser.parse_args()

    app_path = os.path.join(apps_path, args.app + ".py")
    logger.info(app_path)

    # StatisticsHandler is used by Receiver to parse metadata information to be displayed in
    # 'statistics' application, all messages are being processed.
    stats = StatisticsHandler(hit_threshold=args.hit_threshold, buffer_size=args.buffer_size)

    # Receiver gets messages via zmq stream, reconstruct images (only those that are being
    # requested), and parses statistics with StatisticsHandler
    receiver = Receiver(on_receive=stats.parse, buffer_size=args.buffer_size)

    # Start receiver in a separate thread
    start_receiver = partial(receiver.start, args.connection_mode, args.address)
    t = Thread(target=start_receiver, daemon=True)
    t.start()

    # StreamvisHandler is a custom bokeh application Handler, which sets some of the core
    # properties for new bokeh documents created by all applications.
    sv_handler = StreamvisHandler(receiver, stats, args)

    applications = dict()  # List of bokeh applications

    # Main application
    handler = ScriptHandler(filename=app_path, argv=args.args)
    applications["/"] = Application(sv_handler, handler)

    # Statistics application
    statistics_handler = ScriptHandler(filename=os.path.join(base_path, "statistics.py"))
    applications["/statistics"] = Application(sv_handler, statistics_handler)

    # Hitrate application
    hitrate_handler = ScriptHandler(filename=os.path.join(base_path, "hitrate.py"))
    applications["/hitrate"] = Application(sv_handler, hitrate_handler)

    server = Server(
        applications, port=args.port, allow_websocket_origin=args.allow_websocket_origin
    )

    server.start()
    server.io_loop.start()


if __name__ == "__main__":
    main()
