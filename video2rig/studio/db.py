"""SQLite storage for the user-facing studio (stdlib only).

Tables: users, sessions, characters, videos, works, exports.
Every connection is short-lived; a module lock serialises writes (one process, one worker thread).
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

LOCK = threading.RLock()
SESSION_DAYS = 14
PBKDF2_ROUNDS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  disabled INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created REAL NOT NULL,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  project TEXT UNIQUE,
  model_path TEXT,
  files_dir TEXT,
  owner_id INTEGER,
  status TEXT NOT NULL DEFAULT 'ready',
  published INTEGER NOT NULL DEFAULT 1,
  remote_job TEXT,
  error TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  character_id INTEGER NOT NULL,
  file TEXT NOT NULL,
  orig_name TEXT NOT NULL,
  size INTEGER NOT NULL,
  info TEXT,
  created REAL NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS works (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  character_id INTEGER NOT NULL,
  video_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  project TEXT NOT NULL,
  result_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting',
  forced INTEGER NOT NULL DEFAULT 0,
  remote_job TEXT,
  stage TEXT,
  error TEXT,
  kf_status TEXT NOT NULL DEFAULT 'none',
  kf_job TEXT,
  kf_error TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(project, result_name)
);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  options TEXT,
  status TEXT NOT NULL DEFAULT 'waiting',
  file TEXT,
  error TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
"""


class DB:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            # Additive migrations (old databases keep working).
            cols = {r[1] for r in c.execute('PRAGMA table_info(characters)')}
            for name, decl in (('orient', 'TEXT'), ('inspect_status', "TEXT NOT NULL DEFAULT 'none'"), ('inspect', 'TEXT'),
                               ('prev_projects', 'TEXT'), ('pending_project', 'TEXT'), ('remake_error', 'TEXT')):
                if name not in cols:
                    c.execute(f'ALTER TABLE characters ADD COLUMN {name} {decl}')

    def conn(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=15000')
        return _Conn(c)

    def one(self, sql, *args):
        with self.conn() as c:
            row = c.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, *args):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def run(self, sql, *args):
        with LOCK, self.conn() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid

    def update(self, table, row_id, **fields):
        if not fields:
            return
        if table in ('characters', 'works', 'exports'):
            fields.setdefault('updated', time.time())
        cols = ', '.join(f'{k} = ?' for k in fields)
        self.run(f'UPDATE {table} SET {cols} WHERE id = ?', *fields.values(), row_id)

    # ------------------------------------------------------------------ users
    def create_user(self, username, password, role='user'):
        salt = secrets.token_hex(16)
        with LOCK, self.conn() as c:
            cur = c.execute('INSERT INTO users (username, pw_hash, salt, role, created) VALUES (?, ?, ?, ?, ?)',
                            (username, hash_password(password, salt), salt, role, time.time()))
            return cur.lastrowid

    def set_password(self, user_id, password):
        salt = secrets.token_hex(16)
        self.run('UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?', hash_password(password, salt), salt, user_id)
        self.run('DELETE FROM sessions WHERE user_id = ?', user_id)

    def check_login(self, username, password):
        u = self.one('SELECT * FROM users WHERE username = ?', username)
        if not u:
            hash_password(password, 'x' * 32)   # equalise timing for unknown names
            return None
        if not hmac.compare_digest(u['pw_hash'], hash_password(password, u['salt'])):
            return None
        return u

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.run('INSERT INTO sessions (token_hash, user_id, created, expires) VALUES (?, ?, ?, ?)',
                 token_hash(token), user_id, now, now + SESSION_DAYS * 86400)
        self.run('UPDATE users SET last_login = ? WHERE id = ?', now, user_id)
        self.run('DELETE FROM sessions WHERE expires < ?', now)
        return token

    def session_user(self, token):
        if not token:
            return None
        row = self.one('SELECT u.*, s.expires FROM sessions s JOIN users u ON u.id = s.user_id '
                       'WHERE s.token_hash = ?', token_hash(token))
        if not row or row['expires'] < time.time() or row['disabled']:
            return None
        return row

    def end_session(self, token):
        if token:
            self.run('DELETE FROM sessions WHERE token_hash = ?', token_hash(token))


class _Conn:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *exc):
        self.c.close()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), PBKDF2_ROUNDS).hex()


def token_hash(token):
    return hashlib.sha256(token.encode('ascii', 'ignore')).hexdigest()


def jdump(value):
    return json.dumps(value, ensure_ascii=False)


def jparse(text, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
