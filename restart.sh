#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "==> 停止旧进程..."
fuser -k 8900/tcp 2>/dev/null || true
fuser -k 5899/tcp 2>/dev/null || true
sleep 1

echo "==> 启动后端 (127.0.0.1:8900)..."
cd "$BACKEND_DIR"
nohup .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8900 \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
sleep 2

echo "==> 启动前端 (0.0.0.0:5899)..."
cd "$FRONTEND_DIR"
nohup npm run dev \
  > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
sleep 3

echo ""
echo "==> 验证服务..."
curl -s http://127.0.0.1:8900/api/health && echo ""
curl -s -o /dev/null -w "前端 HTTP: %{http_code}\n" http://localhost:5899/

echo ""
echo "=============================="
echo "  后端 : http://127.0.0.1:8900"
echo "  前端 : http://localhost:5899"
echo "  后端 PID: $BACKEND_PID"
echo "  前端 PID: $FRONTEND_PID"
echo "  日志目录: $LOG_DIR"
echo "=============================="
