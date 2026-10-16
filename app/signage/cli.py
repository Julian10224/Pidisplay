"""signage-admin — beheer vanaf de opdrachtregel.

Wordt aangeroepen via /usr/local/bin/signage-admin, dat dataopdrachten als gebruiker
'signage' uitvoert (zodat databasebestanden de juiste eigenaar houden).
"""

import argparse
import getpass
import os
import shutil
import sys

from . import __version__, backup, db
from .config import Config


def _conn(cfg):
    if not os.path.exists(cfg.db_path):
        sys.exit(f"Database niet gevonden: {cfg.db_path}")
    return db.connect(cfg.db_path)


def cmd_status(cfg, _args):
    conn = _conn(cfg)
    users = [r[0] for r in conn.execute("SELECT username FROM users")]
    s = db.get_settings(conn)
    print(f"Versie:            {__version__}")
    print(f"Configuratie:      {cfg.path}")
    print(f"Gegevens:          {cfg.data_dir}")
    print(f"Systeemnaam:       {s['system_name']}")
    print(f"Beheerder(s):      {', '.join(users) if users else 'nog niet aangemaakt'}")
    if not users and os.path.exists(cfg.setup_code_path):
        print(f"Setup-code:        {open(cfg.setup_code_path).read().strip()}")
    media = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    pls = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    print(f"Media/playlists:   {media} / {pls}")
    du = shutil.disk_usage(cfg.data_dir)
    print(f"Vrije opslag:      {du.free // (1024 * 1024)} MB")


def cmd_reset_password(cfg, args):
    from werkzeug.security import generate_password_hash

    from .app import PASSWORD_METHOD
    conn = _conn(cfg)
    users = [r[0] for r in conn.execute("SELECT username FROM users")]
    if not users:
        sys.exit("Er is nog geen beheerder. Open de beheerwebsite en gebruik de setup-code van het scherm.")
    username = args.username or (users[0] if len(users) == 1 else None)
    if username not in users:
        sys.exit(f"Onbekende gebruiker. Bestaande gebruikers: {', '.join(users)}")
    pw = getpass.getpass(f"Nieuw wachtwoord voor {username}: ")
    if len(pw) < 8:
        sys.exit("Wachtwoord moet minimaal 8 tekens hebben.")
    if getpass.getpass("Herhaal: ") != pw:
        sys.exit("Wachtwoorden zijn niet gelijk.")
    conn.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                 (generate_password_hash(pw, method=PASSWORD_METHOD), username))
    print("Wachtwoord gewijzigd.")


def cmd_reset_admin(cfg, _args):
    conn = _conn(cfg)
    conn.execute("DELETE FROM users")
    try:
        os.unlink(cfg.setup_code_path)
    except FileNotFoundError:
        pass
    print("Beheerder gewist. Binnen een halve minuut staat een nieuwe setup-code op het scherm.")
    print("Open daarna de beheerwebsite om een nieuwe beheerder aan te maken.")


def cmd_backup(cfg, args):
    name = backup.create_backup(cfg, include_media=args.with_media, prefix="auto-" if args.auto else "")
    if args.auto:
        backup.prune(cfg, "auto-", cfg.auto_backup_keep)
    path = os.path.join(cfg.backups_dir, name)
    if args.output:
        try:
            os.makedirs(args.output, exist_ok=True)
            shutil.copy2(path, os.path.join(args.output, name))
        except OSError as exc:
            sys.exit(f"Back-up gemaakt ({path}), maar kopiëren naar {args.output} mislukte: {exc}")
        path = os.path.join(args.output, name)
    print(path)


def cmd_restore(cfg, args):
    try:
        backup.stage_restore(cfg, args.file)
    except backup.BackupError as exc:
        sys.exit(f"Herstel geweigerd: {exc}")
    print("Back-up gecontroleerd en klaargezet; wordt toegepast bij de volgende start van de server.")


def cmd_setup_code(cfg, _args):
    conn = _conn(cfg)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        sys.exit("Er is al een beheerder; er is geen setup-code meer.")
    try:
        print(open(cfg.setup_code_path).read().strip())
    except FileNotFoundError:
        sys.exit("Nog geen setup-code; start de server (sudo systemctl start signage-server).")


def main(argv=None):
    p = argparse.ArgumentParser(prog="signage-admin", description="Beheer van het signage-systeem.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Toon de status")
    rp = sub.add_parser("reset-password", help="Wachtwoord van de beheerder opnieuw instellen")
    rp.add_argument("username", nargs="?")
    sub.add_parser("reset-admin", help="Beheerder wissen; nieuwe setup via de code op het scherm")
    sub.add_parser("setup-code", help="Toon de setup-code (zolang er geen beheerder is)")
    bp = sub.add_parser("backup", help="Maak een back-up")
    bp.add_argument("--with-media", action="store_true", help="Inclusief alle media")
    bp.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    bp.add_argument("--output", help="Kopieer de back-up ook naar deze map (bijv. een USB-stick)")
    rs = sub.add_parser("restore", help="Zet een back-up terug")
    rs.add_argument("file")
    args = p.parse_args(argv)
    cfg = Config()
    {"status": cmd_status, "reset-password": cmd_reset_password, "reset-admin": cmd_reset_admin,
     "setup-code": cmd_setup_code, "backup": cmd_backup, "restore": cmd_restore}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
