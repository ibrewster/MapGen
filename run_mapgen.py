import logging

from mapgen import app

if __name__ == "__main__":
    logging.basicConfig(level = logging.INFO,
                        format = "%(asctime)-15s %(message)s",
                        datefmt='%Y-%m-%d %H:%M:%S')
    app.run(host = '0.0.0.0', port=5050, debug = False, use_reloader = True)
