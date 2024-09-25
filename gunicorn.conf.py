import os

script_dir = os.path.dirname(__file__)

wsgi_app = "mapgen:app"
chdir = script_dir
user = "www-data"
group = "www-data"
bind = ['unix:/run/mapgen/gunicorn.sock','127.0.0.1:5002']
workers = 1
threads = 100
worker_connections = 102
timeout = 300
accesslog = "/var/log/mapgen/access.log"
errorlog = "/var/log/mapgen/error.log"
capture_output = True
