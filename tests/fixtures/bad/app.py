import argparse
import os

timeout = os.getenv('TIMEOUT', '60')
parser = argparse.ArgumentParser()
parser.add_argument('--port', default=8000)
