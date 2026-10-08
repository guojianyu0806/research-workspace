"""Local workspace accounts. Each authenticated account owns a separate library."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class AuthenticationError(ValueError):
    pass


class Accounts:
    COOKIE = 'research_session'

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'accounts.sqlite3'
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT UNIQUE,salt BLOB,digest BLOB,created REAL);
              CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,user_id TEXT,expires REAL);
              CREATE TABLE IF NOT EXISTS login_attempts(name TEXT PRIMARY KEY,failures INTEGER,started REAL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def derive(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)

    def register(self, name: str, password: str):
        name = name.strip()
        if not re.fullmatch(r'[\w\-]{2,40}',name) or not 10 <= len(password) <= 200:
            raise ValueError('用户名需2至40个字母、数字、汉字或下划线；密码需10至200个字符。')
        salt, user_id = secrets.token_bytes(16), uuid4().hex
        digest = self.derive(password,salt)
        try:
            with self.connection() as db:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(user_id,name,salt,digest,time.time()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('该用户名已被使用。') from exc
        return self.new_session(user_id), {'id':user_id,'name':name,'mode':'account'}

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+7*86400))
        return token

    def login(self,name,password):
        name = name.strip()
        if len(name)>40 or len(password)>200:
            raise AuthenticationError('用户名或密码不正确。')
        now = time.time()
        with self.connection() as db:
            attempt = db.execute('SELECT * FROM login_attempts WHERE name=?',(name,)).fetchone()
            if attempt and attempt['failures'] >= 10 and now-attempt['started'] < 900:
                raise AuthenticationError('尝试次数过多，请15分钟后再试。')
            user = db.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
            salt = user['salt'] if user else b'no-account-salt0'
            digest = self.derive(password,salt)
            if not user or not hmac.compare_digest(digest,user['digest']):
                failures = attempt['failures']+1 if attempt and now-attempt['started']<900 else 1
                started = attempt['started'] if attempt and now-attempt['started']<900 else now
                db.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(name,failures,started))
                db.commit()
                raise AuthenticationError('用户名或密码不正确。')
            db.execute('DELETE FROM login_attempts WHERE name=?',(name,))
            safe_user = {'id':user['id'],'name':user['name'],'mode':'account'}
        return self.new_session(safe_user['id']), safe_user

    def resolve(self, token):
        if not token:
            return {'id':None,'name':'本机默认空间','mode':'local'}
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            user = db.execute('SELECT users.id,users.name FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.digest=? AND sessions.expires>?',(digest,time.time())).fetchone()
        if not user:
            raise AuthenticationError('登录已过期，请重新登录。')
        return dict(user) | {'mode':'account'}

    def logout(self,token):
        if token:
            with self.connection() as db:
                db.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def directory(self,user):
        return self.root / 'spaces' / user['id'] if user['id'] else self.root

    def workspace_directories(self):
        with self.connection() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM users')]
        return [self.root]+[self.root / 'spaces' / user_id for user_id in ids]
