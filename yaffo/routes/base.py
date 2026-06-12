from flask import Flask, send_from_directory , request

def init_base_routes(app: Flask):
    @app.route('/favicon.ico', methods=["GET"])
    def favicon():
        theme = request.args.get('theme', 'classic')
        location = f'static/themes/{theme}'
        return send_from_directory(location, 'favicon.svg')