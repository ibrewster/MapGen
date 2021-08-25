from gevent import monkey
monkey.patch_all()

import logging

from mapgen import app


if __name__ == "__main__":
    logging.basicConfig(level = logging.INFO)
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    server = pywsgi.WSGIServer(('', 5000), app, handler_class=WebSocketHandler)
    server.serve_forever()
    # app.run(host = '0.0.0.0', debug = False, use_reloader = True)
