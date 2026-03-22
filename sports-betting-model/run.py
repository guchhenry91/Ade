#!/usr/bin/env python3
"""
Start the Sports Betting Model web app.

Usage:
    python run.py               # http://localhost:8000
    python run.py --port 5000   # custom port
    python run.py --reload      # dev mode with auto-reload
"""
import argparse
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

def main():
    parser = argparse.ArgumentParser(description="Sports Betting Model Web App")
    parser.add_argument("--host",   default="0.0.0.0",  help="Host (default: 0.0.0.0)")
    parser.add_argument("--port",   default=8000, type=int, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true",    help="Auto-reload on file changes")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("Installing uvicorn...")
        os.system(f"{sys.executable} -m pip install uvicorn fastapi jinja2 python-multipart")
        import uvicorn

    print(f"\n{'='*50}")
    print(f"  Sports Betting Model")
    print(f"  http://localhost:{args.port}")
    print(f"{'='*50}\n")
    print("  ⚽ Soccer  → /soccer")
    print("  🏀 NBA     → /nba")
    print("  🏈 NFL     → /nfl")
    print("  ⚡ Demo    → /demo")
    print("  📊 Reports → /reports\n")

    uvicorn.run(
        "app.server:app",
        host    = args.host,
        port    = args.port,
        reload  = args.reload,
        log_level = "info",
    )

if __name__ == "__main__":
    main()
