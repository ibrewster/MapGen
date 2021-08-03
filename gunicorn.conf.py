import os

script_dir = os.path.dirname(__file__)

wsgi_app = "mapgen:app"
chdir = script_dir
user = "mapgen"
group = "nginx"
bind = ['unix:/var/run/mapgen/gunicorn.sock']
workers = 2
raw_env = ["SCRIPT_NAME=/mapgen"]
