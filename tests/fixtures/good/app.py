import argparse
import os

timeout = os.getenv('TIMEOUT', '30')
parser = argparse.ArgumentParser()
parser.add_argument('--port', default=8080)
