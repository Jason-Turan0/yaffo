rm -rf /tmp/yaffo-wheel-test
rm -rf /tmp/yaffo-wheel-state
rm -rf dist build *.egg-info

python -m pip install --upgrade build
python -m build

python3.13 -m venv /tmp/yaffo-wheel-test
source /tmp/yaffo-wheel-test/bin/activate
python -m pip install --upgrade pip
python -m pip install dist/yaffo-*.whl

python -c "import yaffo.routes, yaffo.taskq.host, yaffo.scripts.db.migrate"
cd /tmp/yaffo-wheel-test
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state yaffo