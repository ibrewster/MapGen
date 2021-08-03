import os

script_dir = os.path.dirname(__file__)

wsgi_app = "mapgen:app"
chdir = script_dir
user = "mapgen"
group = "nginx"
bind = ['unix:/var/run/mapgen/gunicorn.sock', '0.0.0.0:8000']
workers = 2
