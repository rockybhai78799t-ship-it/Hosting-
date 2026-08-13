#!/bin/bash
# start.sh

echo "🚀 Starting Hosting Bot..."

# Check Python version
python3 --version

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Run the bot
python3 sexx.py
