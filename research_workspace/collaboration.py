"""Opt-in local collaborative reranking of the caller's own public candidates.

Never enumerate other workspace directories or read their discovery/library DBs.
The shared projection contains only account UUIDs, canonical arXiv IDs and votes.
It is not differential privacy; participation is explicit and withdrawable.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import re
import sqlite3

from .library import _schema_lock

MAX_PROFILE=500
MAX_PARTICIPANTS=500
MAX_NEIGHBORS=100
MIN_COMMON=2
MIN_COHORT=3
ID=re.compile(r'^arxiv:((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7}))(?:v[1-9]\d*)?$')


def canonical(paper_id):
    match=ID.fullmatch(paper_id) if isinstance(paper_id,str) else None
    return match[1] if match else None


class CollaborativeRecommendations:
    def __init__(self,accounts,user,library):
        self.accounts=accounts
        self.user_id=user.get('id')
        self.library=library
        if library.data_dir.resolve()!=accounts.directory(user).resolve():
            raise ValueError('推荐工作空间与当前账号不一致。')
        if self.user_id:
            with accounts.connection() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=?',(self.user_id,)).fetchone():
                    raise ValueError('当前推荐账号不存在。')
        self.path=accounts.root/'collaboration.sqlite3'

    @contextmanager
    def _db(self,write=False):
        if write:
            with _schema_lock(self.path):
                with sqlite3.connect(self.path,timeout=20) as schema:
                    schema.executescript('''
                      CREATE TABLE IF NOT EXISTS participants(user_id TEXT PRIMARY KEY, profile_hash TEXT NOT NULL);
                      CREATE TABLE IF NOT EXISTS signals(user_id TEXT NOT NULL,paper_id TEXT NOT NULL,value INTEGER NOT NULL CHECK(value IN (-1,1)),PRIMARY KEY(user_id,paper_id));
                      CREATE INDEX IF NOT EXISTS signals_paper ON signals(paper_id,value,user_id);
                    ''')
        db=sqlite3.connect(self.path if write else self.path.resolve().as_uri()+'?mode=ro',uri=not write,timeout=20)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            if write:db.commit()
        except BaseException:
            if write:db.rollback()
            raise
        finally:db.close()

    def _settings(self,db=None):
        joined=db.execute('SELECT 1 FROM participants WHERE user_id=?',(self.user_id,)).fetchone() if db and self.user_id else None
        count=db.execute('SELECT count(*) FROM signals WHERE user_id=?',(self.user_id,)).fetchone()[0] if joined else 0
        return {'can_join':bool(self.user_id),'enabled':bool(joined),'shared_feedback_count':count,
            'profile_limit':MAX_PROFILE,'minimum_common_likes':MIN_COMMON,'minimum_group_size':MIN_COHORT,
            'policy':'仅共享当前账号已反馈的公开arXiv论文标识和喜欢/不喜欢；不共享文献、查询、笔记或任务。退出后清除本账号协同投影，个人反馈保留。',
            'candidate_scope':'仅重排本工作空间已缓存的公开论文，不从其他账号获取文献或元数据。',
            'privacy_notice':'不展示其他账号身份或逐人反馈；最小群组限制不等于差分隐私保证。'}

    def settings(self):
        if not self.path.exists() or not self.user_id:return self._settings()
        with self._db() as db:return self._settings(db)

    def _profile(self,discovery):
        if discovery.library.data_dir.resolve()!=self.library.data_dir.resolve():
            raise ValueError('反馈来源不属于当前工作空间。')
        profile={}
        with discovery._db() as db:
            rows=db.execute('''SELECT p.paper_id,p.value,r.record FROM preferences p JOIN papers r ON r.id=p.paper_id
                ORDER BY p.updated_at DESC,p.paper_id DESC LIMIT 10000''')
            for row in rows:
                key=canonical(row['paper_id'])
                if not key or key in profile:continue
                try:record=json.loads(row['record'])
                except (ValueError,TypeError):continue
                identifier=row['paper_id'][6:]
                if not isinstance(record,dict) or not (record.get('metadata_verified') is True and record.get('source')=='arxiv'
                    and record.get('id')==row['paper_id'] and record.get('canonical_id')==key
                    and record.get('source_url')=='https://arxiv.org/abs/'+identifier):continue
                # A newer zero clears older version votes; versions never act as
                # separate users or independent common interests.
                profile[key]=row['value']
                if len(profile)>=MAX_PROFILE:break
        return {key:value for key,value in profile.items() if value in {-1,1}}

    def _publish(self,db,discovery):
        profile=self._profile(discovery)
        fingerprint=hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()
        previous=db.execute('SELECT profile_hash FROM participants WHERE user_id=?',(self.user_id,)).fetchone()
        if previous and previous[0]!=fingerprint:
            db.execute('DELETE FROM signals WHERE user_id=?',(self.user_id,))
            db.executemany('INSERT INTO signals VALUES(?,?,?)',[(self.user_id,key,value) for key,value in profile.items()])
            db.execute('UPDATE participants SET profile_hash=? WHERE user_id=?',(fingerprint,self.user_id))

    def set_enabled(self,enabled,discovery):
        if type(enabled) is not bool:raise ValueError('协同推荐开关必须为布尔值。')
        if not self.user_id:raise ValueError('请登录独立账号后再加入协同推荐。')
        with self._db(write=True) as db:
            if enabled:
                exists=db.execute('SELECT 1 FROM participants WHERE user_id=?',(self.user_id,)).fetchone()
                if not exists and db.execute('SELECT count(*) FROM participants').fetchone()[0]>=MAX_PARTICIPANTS:
                    raise ValueError('当前本地协同推荐已达到账号容量上限。')
                db.execute("INSERT OR IGNORE INTO participants VALUES(?,'')",(self.user_id,))
                self._publish(db,discovery)
            else:
                db.execute('DELETE FROM signals WHERE user_id=?',(self.user_id,))
                db.execute('DELETE FROM participants WHERE user_id=?',(self.user_id,))
            return self._settings(db)

    def sync(self,discovery):
        if not self.user_id or not self.path.exists():return self.settings()
        # Recheck participation while holding the writer lock: an in-flight
        # feedback request cannot re-publish signals after an account withdraws.
        with self._db(write=True) as db:
            if db.execute('SELECT 1 FROM participants WHERE user_id=?',(self.user_id,)).fetchone():
                self._publish(db,discovery)
            return self._settings(db)

    def rerank(self,result,discovery):
        settings=self.sync(discovery)
        status='disabled' if self.user_id else 'local_space_excluded'
        changes={}
        if settings['enabled']:
            with self._db() as db:
                own=dict(db.execute('SELECT paper_id,value FROM signals WHERE user_id=?',(self.user_id,)))
                positives={key for key,value in own.items() if value==1}
                status='insufficient_profile'
                if len(positives)>=MIN_COMMON:
                    marks=','.join('?' for _ in positives)
                    neighbors=db.execute(f'''SELECT s.user_id,count(*) AS common FROM signals s JOIN participants p ON p.user_id=s.user_id
                        WHERE s.value=1 AND s.user_id!=? AND s.paper_id IN ({marks}) GROUP BY s.user_id
                        HAVING count(*)>=? ORDER BY common DESC,s.user_id LIMIT ?''',
                        (self.user_id,*sorted(positives),MIN_COMMON,MAX_NEIGHBORS)).fetchall()
                    status='insufficient_cohort'
                    if len(neighbors)>=MIN_COHORT:
                        votes={}
                        for neighbor in neighbors:
                            profile=dict(db.execute('SELECT paper_id,value FROM signals WHERE user_id=? LIMIT ?',
                                (neighbor['user_id'],MAX_PROFILE)))
                            liked=sum(value==1 for value in profile.values())
                            common=neighbor['common']
                            similarity=common/math.sqrt(len(positives)*liked)*(common/(common+2))
                            for key,value in profile.items():votes.setdefault(key,[]).append((value,similarity))
                        for key,signals in votes.items():
                            if key in own or len(signals)<MIN_COHORT:continue
                            numerator=sum(value*weight for value,weight in signals)
                            direction=1 if numerator>0 else -1
                            if sum(value==direction for value,_ in signals)<MIN_COHORT:continue
                            denominator=sum(weight for _,weight in signals)
                            if denominator:changes[key]=round(.75*numerator/denominator,6)
                        status='no_eligible_signals'
        applied=False
        for item in result['items']:
            key=canonical(item['id'])
            # Caller candidates and their local title/metadata/source fields are
            # never replaced by a neighbor's copy. Explicit local votes win.
            delta=changes.get(key,0) if item.get('feedback',0)==0 else 0
            if delta:
                item['score']=round(item['score']+delta,6)
                item['reasons']=[*item['reasons'],('参与协同的相近兴趣群组倾向喜欢这篇公开论文，适度提高排序。' if delta>0 else
                    '参与协同的相近兴趣群组存在负向反馈，适度降低排序。')]
                applied=True
        if applied:
            result['items'].sort(key=lambda item:(-item['score'],item['id']))
            status='applied'
            result['method']+=' 另依据自愿参与账号的共同喜欢记录进行受限群体重排；不代表论文质量评定。'
        result['collaboration']={**settings,'status':status,'method':'positive_overlap_cosine_shrinkage_v1',
            'network_used':False,'privacy_scope':'opt_in_public_id_votes_only; no_neighbor_identity_or_individual_votes'}
        return result
