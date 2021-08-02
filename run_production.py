import argparse
import os
import subprocess
import sys
import signal
import io


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('uwsgi')
    args = parser.parse_args()
    uwsgi = args.uwsgi

    comm_pipe = io.BytesIO()

    top_dir =os.path.dirname(__file__)
    code_dir = os.path.join(top_dir, 'mapgen')
    generator = os.path.join(code_dir, 'generate_map.py')
    gen_proc = subprocess.Popen([sys.executable, generator], stdout=subprocess.PIPE)

    comm_pipe = gen_proc.stdout

    uwsgi_ini_file = os.path.join(top_dir, 'mapgen-dev.ini')
    uwsgi_proc = subprocess.Popen([uwsgi, '-i', uwsgi_ini_file], stdout=comm_pipe)

    while True:
        try:
            print(comm_pipe.read())
        except KeyboardInterrupt:
            break

    print("***Closing down processes***")
    gen_proc.send_signal(signal.SIGINT)
    gen_proc.wait()
    print("***Generator process killed. Killing uwsgi.")
    uwsgi_proc.send_signal(signal.SIGINT)
    uwsgi_proc.wait()
    print("***Process Complete")
