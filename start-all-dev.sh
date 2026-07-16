#!/usr/bin/env bash
set -e

echo "Starting Team Scheduler on port 8081..."
cd /Users/jaysonjohnson/Desktop/Storesight-shift-scheduler-7e4bbeb5303129dbd9acd5af3d4d4a8bead2837b
PORT=8081 TOOL_SLUG=shift-scheduler LOCAL_DEV=1 FLASK_DEBUG=1 python3 main.py > /tmp/team-scheduler.log 2>&1 &
TS_PID=$!
echo "Team Scheduler started (PID $TS_PID)"

echo "Starting Shift Assignments on port 8080..."
cd /Users/jaysonjohnson/shift-assignments
TEAM_SCHEDULER_URL=http://localhost:8081 TOOL_SLUG=qc-shift-assignments LOCAL_DEV=1 FLASK_DEBUG=1 python3 main.py > /tmp/shift-assignments.log 2>&1 &
SA_PID=$!
echo "Shift Assignments started (PID $SA_PID)"

echo "Starting Next.js dev server on port 3000..."
cd /Users/jaysonjohnson/shift-assignments/shift-assignments
NEXT_PUBLIC_API_ORIGIN=http://localhost:8080 npm run dev > /tmp/nextjs.log 2>&1 &
NJ_PID=$!
echo "Next.js started (PID $NJ_PID)"

echo ""
echo "All services started!"
echo "  Team Scheduler: http://localhost:8081"
echo "  Shift Assignments API: http://localhost:8080"
echo "  Next.js Frontend: http://localhost:3000"
echo ""
echo "View logs:"
echo "  Team Scheduler:     tail -f /tmp/team-scheduler.log"
echo "  Shift Assignments:  tail -f /tmp/shift-assignments.log"
echo "  Next.js:            tail -f /tmp/nextjs.log"
echo ""
echo "Stop all: kill $TS_PID $SA_PID $NJ_PID"
echo ""

# Wait for all processes
wait
