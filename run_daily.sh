#!/bin/sh
cd "$(dirname "$0")" && mkdir -p logs && . .venv/bin/activate && python agent.py run >> logs/daily.log 2>&1
