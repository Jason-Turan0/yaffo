# 1. Ensure you have the version you need
pyenv local 3.13.3

# 2. Create the virtual environment in your project folder
python -m venv venv

# 3. Activate it
source venv/bin/activate

pip install -e ".[dev]"