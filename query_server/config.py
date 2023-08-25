import configparser
import os

config = configparser.ConfigParser(interpolation=None)
config.read(os.path.abspath(f"{os.path.dirname(os.path.realpath(__file__))}/../config.ini"))

AUTHZ = config['authz']
QUERY_URL = os.getenv("QUERY_URL", f"http://localhost:{config['DEFAULT']['Port']}")

PORT = config['DEFAULT']['Port']

DEBUG_MODE = False
if os.getenv("DEBUG_MODE", "1") == "1":
    DEBUG_MODE = True
