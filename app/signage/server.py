"""Startpunt van de signage-server (gebruikt door signage-server.service)."""

import logging
import os
import sys


def main():
    logging.basicConfig(level=os.environ.get("SIGNAGE_LOG_LEVEL", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)

    from waitress import serve

    from .app import create_app
    from .config import Config

    cfg = Config()
    os.makedirs(cfg.tmp_dir, exist_ok=True)
    # waitress buffert grote uploads in een tijdelijk bestand; niet in /tmp (tmpfs = RAM).
    os.environ["TMPDIR"] = cfg.tmp_dir
    import tempfile
    tempfile.tempdir = cfg.tmp_dir

    app = create_app(cfg)
    logging.getLogger("signage.server").info("Signage-server luistert op %s:%d", cfg.bind, cfg.port)
    serve(app, host=cfg.bind, port=cfg.port, threads=cfg.threads,
          max_request_body_size=cfg.max_upload_bytes + 1024 * 1024,
          channel_timeout=300, clear_untrusted_proxy_headers=True, ident="signage")


if __name__ == "__main__":
    main()
