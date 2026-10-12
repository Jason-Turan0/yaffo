"""Load the checked-in Flickr example into a service created through the UI.

The test uses this only to provide a valid adapter draft; service creation,
credential entry, OAuth, publishing, and Test stay browser-driven.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from yaffo.app import create_app
from yaffo.db import db
from yaffo.db.repositories import service_builder_repository as services


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--wider", action="store_true")
    parser.add_argument("--request-live-reads", action="store_true")
    parser.add_argument("--ready", action="store_true")
    args = parser.parse_args()
    app_dir = Path(__file__).resolve().parents[2]
    code = (app_dir / "yaffo/site_agents/adapter/examples/flickr_target.star").read_text()
    if args.wider:
        code += '\n\ndef _extra_access():\n    return web_request(CONNECTION, "GET", "https://api.flickr.com/services/rest/extra")\n'
    app = create_app()
    with app.app_context():
        row = services.get_service(db.session, args.slug)
        if row is None:
            raise SystemExit(f"No service exists for slug {slug}")
        analysis = services.save_draft(db.session, row.id, code)
        if not analysis.ok:
            raise SystemExit("Flickr example did not pass adapter analysis: " + "; ".join(map(str, analysis.problems)))
        if args.request_live_reads:
            if not row.test_connection_key:
                raise SystemExit("The service needs a signed-in test account before requesting live reads")
            services.ask_for_live_reads(db.session, row.id, row.test_connection_key, "jturan")
            services.set_builder_status(db.session, row.id, "IN_PROGRESS")
        if args.ready:
            services.set_builder_status(db.session, row.id, "READY")


if __name__ == "__main__":
    main()
