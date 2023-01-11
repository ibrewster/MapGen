import os

script_dir = os.path.dirname(__file__)

wsgi_app = "mapgen:app"
chdir = script_dir
# user = "mapgen"
# group = "nginx"
bind = ['unix:/var/run/mapgen/gunicorn.sock', '0.0.0.0:5000']
workers = 1
threads = 100
#raw_env = ["SCRIPT_NAME=/mapgen"]
#worker_class = 'mapgen.flask_sockets.flask_sockets.worker'
worker_connections = 50
timeout = 300
