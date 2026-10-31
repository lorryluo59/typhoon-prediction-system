#!/usr/bin/env bash
# 启动台风预测系统（默认 http://127.0.0.1:8000）
cd "$(dirname "$0")"
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
