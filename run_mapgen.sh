#!/bin/bash

source ~/.bashrc
conda activate mapgen
cd /data/MapGen
/usr/local/miniconda3/envs/mapgen/bin/gunicorn -c /data/MapGen/gunicorn.conf.py

