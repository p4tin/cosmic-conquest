#!/bin/bash

# Find a Python executable that is >= 3.10
PYTHON_EXEC=""
for cmd in "/opt/homebrew/bin/python3" "python3.14" "python3.13" "python3.12" "python3.11" "python3.10" "python3"; do
    if command -v "$cmd" >/dev/null 2>&1; then
        # Check if version is >= 3.10
        VERSION=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -gt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; }; then
            PYTHON_EXEC="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_EXEC" ]; then
    echo "Could not find a Python installation >= 3.10. Installing Python via Homebrew..."
    brew install python
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_EXEC="python3"
    fi
    
    if [ -z "$PYTHON_EXEC" ]; then
        echo "Error: Could not find or install a Python version >= 3.10."
        exit 1
    fi
fi

echo "Creating virtual environment in venv using $PYTHON_EXEC..."
$PYTHON_EXEC -m venv venv

echo "Upgrading pip..."
./venv/bin/pip install --upgrade pip

echo "Installing dependencies from requirements.txt..."
./venv/bin/pip install -r requirements.txt

<<<<<<< Updated upstream
echo "Setup complete. To activate the environment, run: source venv/bin/activate"
=======
echo "Setup complete. To activate the environment, run: source venv/bin/activate"

>>>>>>> Stashed changes
