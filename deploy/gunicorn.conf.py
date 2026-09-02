"""Gunicorn configuration.

Used by supervisor in both dev and production so the two environments differ
only in their .env values, not in how the app is served.
"""
import multiprocessing
import os

# Loopback TCP. Like a unix socket this is unreachable from outside the
# machine, but it avoids the socket's permission trap: gunicorn chowns the
# socket to its own uid/gid after binding, so nginx can only connect when
# the app user and the nginx user share a group. Using a unix socket instead
# means adding one to the other's group and setting `group` below.
bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8000')

# The familiar cpu_count*2+1 formula assumes sync workers, where a worker
# blocks for the whole request. With gthread each worker handles `threads`
# requests concurrently, so that formula massively over-provisions: every
# worker is a separate process holding its own copy of the app (~70 MB
# here), and on a small VM the memory cost dwarfs any throughput gain.
#
# workers x threads is the concurrency ceiling. 4 x 4 = 16 in-flight
# requests is far more than a content site needs; raise it from measurement,
# not from core count.
workers = int(os.environ.get('GUNICORN_WORKERS')
              or max(2, min(multiprocessing.cpu_count(), 4)))
# Threads help because request time is dominated by waiting on Postgres.
threads = int(os.environ.get('GUNICORN_THREADS') or 4)
worker_class = 'gthread'

timeout = int(os.environ.get('GUNICORN_TIMEOUT') or 30)
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically so a slow leak cannot accumulate unbounded.
max_requests = 1000
max_requests_jitter = 100

# Only relevant for a unix-socket bind: connecting needs WRITE permission,
# so a default 0755 socket yields a permission-denied 502 that looks like
# the app being down.
umask = 0o007

# nginx buffers request bodies and terminates TLS, so gunicorn only ever
# talks to a trusted local peer.
forwarded_allow_ips = '127.0.0.1'

accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
# %(D)s is request time in microseconds -- the field you actually want when
# something is slow.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus "%(f)s" "%(a)s"'

proc_name = 'trulyverdant'
preload_app = False   # keep False so a rolling restart picks up new code
