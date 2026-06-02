#!/bin/bash
pip install --no-cache-dir -r requirements.txt
pip uninstall -y gevent gevent-websocket 2>/dev/null || true
