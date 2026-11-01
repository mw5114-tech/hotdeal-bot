"""Read the existing Oracle receiver inbox; never poll Telegram or alter its DB.

Python standard library only. Separate durable SQLite journal and systemd unit.
No retries after an ambiguous GitHub POST, including backlog-only requests.
"""
import base64
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib import request
from urllib.parse import urlsplit
import uuid

REPO = 'mw5114-tech/hotdeal-bot'
WORKFLOW = 'daangn-jirum-manual-fifo.yml'
API = f'https://api.github.com/repos/{REPO}'


def coupang_url(raw):
    try:
        u = urlsplit(raw.rstrip('),，。').replace('&amp;', '&'))
        if u.scheme != 'https' or u.username or u.password or u.port:
            return None
        if ((u.hostname == 'link.coupang.com' and re.fullmatch(r'/a/[A-Za-z0-9]+/?', u.path))
            or (u.hostname in ('coupang.com', 'www.coupang.com') and re.fullmatch(r'/vp/products/\d+/?', u.path))):
            return u.geturl()
    except (ValueError, AttributeError):
        pass
    return None


def classify(row, group, sender, source_id=None):
    if (str(row['chat_id']) != str(group) or row['sender_id'] != int(sender)
        or not row['sender_is_bot'] or row['kind'] != 'live'):
        return None
    text = row['text']
    urls = re.findall(r'https://[^\s<>"\']+', text)
    urls += [e['url'] for e in json.loads(row['entities']) if e.get('type') == 'text_link' and isinstance(e.get('url'), str)]
    link = next((u for u in map(coupang_url, urls) if u), None)
    if not link:
        return None
    if source_id is None:
        match = re.search(r'(?:https://)?t\.me/(?:s/)?jirum/(\d+)', text)
        source_id = int(match[1]) if match else None
    if source_id is not None and source_id <= 94743:
        return None
    return dict(chat_id=str(group), message_id=row['message_id'], source_id=source_id,
                text=text, url=link)


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
          CREATE TABLE IF NOT EXISTS attempts(
            request_id TEXT PRIMARY KEY, event_key TEXT, status TEXT NOT NULL,
            created REAL NOT NULL, run_id INTEGER);
          CREATE UNIQUE INDEX IF NOT EXISTS single_active ON attempts((1)) WHERE status!='completed';
        ''')

    def bind(self, group, sender, after):
        identity = json.dumps([str(group), int(sender), int(after)])
        with self.db:
            old = self.db.execute("SELECT value FROM config WHERE key='identity'").fetchone()
            if old and old[0] != identity:
                raise RuntimeError('IDENTITY_OR_BASELINE_CHANGED_REVIEW_REQUIRED')
            self.db.execute("INSERT OR IGNORE INTO config VALUES('identity',?)", (identity,))

    def add(self, payload):
        key = f"{payload['chat_id']}/{payload['message_id']}"
        with self.db:
            return self.db.execute('INSERT OR IGNORE INTO events(event_key,payload) VALUES(?,?)',
                                   (key, json.dumps(payload, ensure_ascii=False))).rowcount


def read_inbox(journal, inbox_path, group, sender, after, relay_path=None):
    """Read-only connections coexist with the receiver's WAL writer.

    No cursor advance past edits: bounded ascending scan skips durable receipts.
    Optional relay outbox joins Telegram group message IDs to @jirum source IDs.
    """
    source_map = {}
    if relay_path:
        with closing(sqlite3.connect(Path(relay_path).resolve().as_uri()+'?mode=ro', uri=True)) as relay:
            source_map = {r[0]:r[1] for r in relay.execute(
                "SELECT remote_id,post_id FROM delivery WHERE scope='live' AND chat=? AND status='sent' AND remote_id IS NOT NULL", (str(group),))}
    with closing(sqlite3.connect(Path(inbox_path).resolve().as_uri()+'?mode=ro', uri=True)) as source:
        source.row_factory = sqlite3.Row
        rows = source.execute('SELECT * FROM inbox WHERE chat_id=? AND message_id>? ORDER BY message_id', (int(group), int(after)))
        added = 0
        for row in rows:
            payload = classify(row, group, sender, source_map.get(row['message_id']))
            if payload:
                added += journal.add(payload)
        return added


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('GITHUB_REDIRECT_BLOCKED')


class Github:
    def __init__(self, token):
        self.token = token
        self.opener = request.build_opener(NoRedirect())

    def call(self, path, payload=None):
        req = request.Request(API+path, data=None if payload is None else json.dumps(payload).encode(), headers={
            'Authorization':'Bearer '+self.token, 'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2022-11-28', 'User-Agent':'HotdealOracleDispatcher/1',
            'Content-Type':'application/json'})
        with self.opener.open(req, timeout=25) as res:
            if payload is not None:
                if res.status != 204:
                    raise RuntimeError('DISPATCH_NOT_CONFIRMED')
                return None
            if res.status != 200:
                raise RuntimeError('READ_NOT_CONFIRMED')
            return json.load(res)

    def state(self):
        data = self.call('/contents/state/daangn_jirum_manual_fifo.json?ref=main')
        state = json.loads(base64.b64decode(data['content']))
        if state.get('schema') != 'daangn-jirum-manual-fifo-v1' or not isinstance(state.get('pending'), list) or 'in_flight' not in state:
            raise RuntimeError('FIFO_SCHEMA_INVALID')
        return state

    def runs(self):
        return self.call(f'/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100')['workflow_runs']

    def dispatch(self, rid, payload):
        return self.call(f'/actions/workflows/{WORKFLOW}/dispatches',
                         dict(ref='main',inputs=dict(request_id=rid,telegram_payload=payload)))


def tick(journal, github, enabled=False, now=None, cap=12):
    if not enabled:
        return 'CAPTURE_ONLY'
    if type(cap) is not int or not 1 <= cap <= 12:
        raise ValueError('DAILY_CAP_INVALID')
    now = time.time() if now is None else now
    db = journal.db
    active = db.execute("SELECT * FROM attempts WHERE status!='completed'").fetchone()
    runs = None
    if active:
        if active['status'] == 'uncertain':
            return 'DISPATCH_UNCERTAIN_REVIEW_REQUIRED'
        runs = github.runs()
        matches = [r for r in runs if r.get('display_title') == 'telegram-'+active['request_id']]
        if len(matches) != 1:
            return 'DISPATCH_VISIBILITY_REVIEW_REQUIRED'
        run = matches[0]
        if run['status'] != 'completed':
            return 'WORKFLOW_IN_PROGRESS'
        if run['conclusion'] != 'success':
            return 'FAILED_RUN_REVIEW_REQUIRED'
        with db:
            db.execute("UPDATE attempts SET status='completed',run_id=? WHERE request_id=?", (run['id'],active['request_id']))
    state = github.state()
    if state['in_flight'] is not None:
        return 'POST_REVIEW_REQUIRED'
    event = db.execute("SELECT * FROM events WHERE status='pending' ORDER BY seq LIMIT 1").fetchone()
    if not event and not state['pending']:
        return 'IDLE'
    runs = github.runs() if runs is None else runs
    own = [r for r in runs if r.get('head_branch')=='main' and r.get('event') in ('workflow_dispatch','push')]
    if any(r['status']!='completed' for r in own):
        return 'WORKFLOW_IN_PROGRESS'
    if own and max(own,key=lambda r:r['created_at']).get('conclusion')!='success':
        return 'FAILED_RUN_REVIEW_REQUIRED'
    def timestamp(run):
        return datetime.fromisoformat(run['created_at'].replace('Z','+00:00')).timestamp()
    if sum(0 <= now-timestamp(r) < 86400 for r in own) >= cap:
        return 'DAILY_CAP'
    # Journal budget additionally guards against GitHub listing delays.
    if db.execute('SELECT count(*) FROM attempts WHERE created>?',(now-86400,)).fetchone()[0]>=cap:
        return 'DAILY_CAP'
    rid = str(uuid.uuid4())
    with db:
        db.execute("INSERT INTO attempts VALUES(?,?,'uncertain',?,NULL)", (rid,event['event_key'] if event else None,now))
    # Any exception/crash here keeps the unique active row forever for review.
    github.dispatch(rid,event['payload'] if event else '{}')
    with db:
        db.execute("UPDATE attempts SET status='accepted' WHERE request_id=?",(rid,))
        if event:
            db.execute("UPDATE events SET status='dispatched' WHERE event_key=?",(event['event_key'],))
    return 'DISPATCH_ACCEPTED'


def main():
    import fcntl
    path = Path(os.environ['DISPATCH_DB_PATH'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path)+'.lock','a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal=Journal(path)
        group=os.environ['TELEGRAM_GROUP_CHAT_ID'];sender=int(os.environ['TRUSTED_RELAY_BOT_ID'])
        after=int(os.environ['DISPATCH_AFTER_MESSAGE_ID'])
        journal.bind(group,sender,after)
        github=Github(os.environ.get('GITHUB_TOKEN',''))
        enabled=os.environ.get('ENABLE_DISPATCH')=='true'
        next_check=0
        while True:
            try:
                added=read_inbox(journal,os.environ['RECEIVER_DB_PATH'],group,sender,after,os.environ.get('RELAY_DB_PATH'))
                if added or time.monotonic()>=next_check:
                    print(json.dumps(dict(status=tick(journal,github,enabled),new_events=added)),flush=True)
                    next_check=time.monotonic()+60
            except Exception as exc:
                print(json.dumps(dict(status='CHECK_FAILED',error_type=type(exc).__name__)),flush=True)
            time.sleep(5)


if __name__=='__main__': main()
