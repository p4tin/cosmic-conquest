#!/bin/bash

echo "Creating virtual environment in /Users/p4tin/Documents/tmp/venv..."
python3 -m venv venv

echo "Upgrading pip..."
./venv/bin/pip install --upgrade pip

echo "Installing dependencies from requirements.txt..."
./venv/bin/pip install -r requirements.txt

echo "Setup complete. To activate the environment, run: source venv/bin/activate"