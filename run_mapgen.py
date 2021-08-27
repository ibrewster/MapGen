import logging

from mapgen import app

if __name__ == "__main__":
    logging.basicConfig(level = logging.INFO,
                        format = "%(asctime)-15s %(message)s",
                        datefmt='%Y-%m-%d %H:%M:%S')
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    server = pywsgi.WSGIServer(('', 5002), app, handler_class=WebSocketHandler)
    server.serve_forever()
    # app.run(host = '0.0.0.0', debug = False, use_reloader = True)
