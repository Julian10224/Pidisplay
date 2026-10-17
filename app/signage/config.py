"""Centrale configuratie.

Alle instellingen op systeemniveau (poort, paden, limieten) staan in één INI-bestand,
standaard /etc/signage/signage.ini. Het pad kan worden overschreven met de
omgevingsvariabele SIGNAGE_CONFIG. Ontbrekende opties krijgen de standaardwaarden
hieronder, zodat een oud configuratiebestand na een update blijft werken.

Instellingen die je via de beheerwebsite wijzigt (systeemnaam, standaardduur,
standaardplaylist, volume) staan in de database, niet in dit bestand.
"""

import configparser
import os

DEFAULT_CONFIG_PATH = "/etc/signage/signage.ini"

DEFAULTS = {
    "server": {
        "bind": "0.0.0.0",
        "port": "80",
        "threads": "6",
        "allow_public_clients": "false",
    },
    "storage": {
        "data_dir": "/var/lib/signage",
        "max_upload_mb": "2048",
        "min_free_mb": "500",
    },
    "media": {
        "max_image_width": "1920",
        "max_image_height": "1080",
        "max_image_megapixels": "40",
    },
    "security": {
        "session_hours": "12",
    },
    "system": {
        "priv_helper": "/usr/local/sbin/signage-priv",
        "local_player_config": "/etc/signage/player.ini",
        "auto_backup_keep": "14",
    },
    "update": {
        "enabled": "true",
        "repo": "Julian10224/Pidisplay",
        "branch": "main",
    },
}


class Config:
    def __init__(self, path=None):
        self.path = path or os.environ.get("SIGNAGE_CONFIG", DEFAULT_CONFIG_PATH)
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read_dict(DEFAULTS)
        if os.path.exists(self.path):
            self.parser.read(self.path, encoding="utf-8")

    def get(self, section, key):
        return self.parser.get(section, key)

    def getint(self, section, key):
        return self.parser.getint(section, key)

    def getbool(self, section, key):
        return self.parser.getboolean(section, key)

    # Handige afgeleide waarden -------------------------------------------------
    @property
    def bind(self):
        return self.get("server", "bind")

    @property
    def port(self):
        return self.getint("server", "port")

    @property
    def threads(self):
        return self.getint("server", "threads")

    @property
    def allow_public_clients(self):
        return self.getbool("server", "allow_public_clients")

    @property
    def data_dir(self):
        return self.get("storage", "data_dir")

    @property
    def db_path(self):
        return os.path.join(self.data_dir, "signage.db")

    @property
    def media_dir(self):
        return os.path.join(self.data_dir, "media")

    @property
    def files_dir(self):
        return os.path.join(self.media_dir, "files")

    @property
    def renditions_dir(self):
        return os.path.join(self.media_dir, "renditions")

    @property
    def thumbs_dir(self):
        return os.path.join(self.media_dir, "thumbs")

    @property
    def html_dir(self):
        return os.path.join(self.media_dir, "html")

    @property
    def incoming_dir(self):
        return os.path.join(self.media_dir, ".incoming")

    @property
    def backups_dir(self):
        return os.path.join(self.data_dir, "backups")

    @property
    def tmp_dir(self):
        return os.path.join(self.data_dir, "tmp")

    @property
    def standby_path(self):
        return os.path.join(self.data_dir, "standby.png")

    @property
    def secret_key_path(self):
        return os.path.join(self.data_dir, "secret_key")

    @property
    def setup_code_path(self):
        return os.path.join(self.data_dir, "setup_code")

    @property
    def restore_pending_path(self):
        return os.path.join(self.data_dir, "restore-pending.tar.gz")

    @property
    def max_upload_bytes(self):
        return self.getint("storage", "max_upload_mb") * 1024 * 1024

    @property
    def min_free_bytes(self):
        return self.getint("storage", "min_free_mb") * 1024 * 1024

    @property
    def max_image_size(self):
        return (self.getint("media", "max_image_width"), self.getint("media", "max_image_height"))

    @property
    def max_image_pixels(self):
        return self.getint("media", "max_image_megapixels") * 1_000_000

    @property
    def session_hours(self):
        return self.getint("security", "session_hours")

    @property
    def priv_helper(self):
        return self.get("system", "priv_helper").strip()

    @property
    def local_player_config(self):
        return self.get("system", "local_player_config").strip()

    @property
    def auto_backup_keep(self):
        return self.getint("system", "auto_backup_keep")

    def ensure_dirs(self):
        for d in (self.data_dir, self.media_dir, self.files_dir, self.renditions_dir,
                  self.thumbs_dir, self.html_dir, self.incoming_dir, self.backups_dir, self.tmp_dir):
            os.makedirs(d, exist_ok=True)


def read_player_config(path):
    """Lees het player-configuratiebestand (gedeeld door server en player)."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_dict({"player": {
        "server_url": "http://127.0.0.1:80",
        "token": "",
        "cache_dir": "/var/lib/signage/player",
        "mpv_binary": "mpv",
        "hwdec": "v4l2m2m-copy",
        "vo": "gpu",
        "gpu_context": "drm",
        "audio_device": "auto",
        "extra_args": "",
        "render_quality": "fast",
        "poll_interval": "5",
        "heartbeat_interval": "15",
        "renderer": "auto",
        "browser_resolution": "auto",
        "local_port": "8765",
        "chromium_binary": "auto",
        "chromium_extra_args": "",
    }})
    if path and os.path.exists(path):
        parser.read(path, encoding="utf-8")
    return parser["player"]
