"""Derived paging index; original job results and paid checkpoints stay intact."""
import json
import math

INDEX_VERSION='translation-pages-v2'

class TranslationRevisionChanged(Exception):
    pass


def create_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS translation_page_meta(
          job_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,dirty INTEGER NOT NULL DEFAULT 1,
          source_kind TEXT,total INTEGER NOT NULL DEFAULT 0,header TEXT,error TEXT,index_version TEXT);
        CREATE TABLE IF NOT EXISTS translation_page_blocks(
          job_id TEXT,ordinal INTEGER,block_id TEXT,payload TEXT,source_chars INTEGER,translation_chars INTEGER,
          source_newlines INTEGER,translation_newlines INTEGER,anchor_valid INTEGER,missing_translation INTEGER,
          PRIMARY KEY(job_id,ordinal));
        CREATE INDEX IF NOT EXISTS translation_block_lookup ON translation_page_blocks(job_id,block_id);
        CREATE TRIGGER IF NOT EXISTS translation_result_changed AFTER UPDATE OF result ON research_jobs
        WHEN NEW.kind='translate' BEGIN
          UPDATE translation_page_meta SET dirty=1,revision=revision+1 WHERE job_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS translation_item_inserted AFTER INSERT ON research_job_items BEGIN
          UPDATE translation_page_meta SET dirty=1,revision=revision+1 WHERE job_id=NEW.job_id;
        END;
        CREATE TRIGGER IF NOT EXISTS translation_item_updated AFTER UPDATE ON research_job_items BEGIN
          UPDATE translation_page_meta SET dirty=1,revision=revision+1 WHERE job_id=NEW.job_id;
        END;
        CREATE TRIGGER IF NOT EXISTS translation_item_deleted AFTER DELETE ON research_job_items BEGIN
          UPDATE translation_page_meta SET dirty=1,revision=revision+1 WHERE job_id=OLD.job_id;
        END;
        CREATE TRIGGER IF NOT EXISTS translation_job_deleted AFTER DELETE ON research_jobs BEGIN
          DELETE FROM translation_page_meta WHERE job_id=OLD.id;
          DELETE FROM translation_page_blocks WHERE job_id=OLD.id;
        END;
    ''')
    # Existing workspaces can retain v1 derived rows across a code upgrade.
    # Migrate only this rebuildable index, never historical model responses.
    if 'index_version' not in {row['name'] for row in db.execute('PRAGMA table_info(translation_page_meta)')}:
        db.execute('BEGIN IMMEDIATE')
        if 'index_version' not in {row['name'] for row in db.execute('PRAGMA table_info(translation_page_meta)')}:
            db.execute('ALTER TABLE translation_page_meta ADD COLUMN index_version TEXT')


def header_row(db,job_id):
    row=db.execute('''SELECT id,kind,status,payload,error,progress,total,cancel_requested,created_at,updated_at,
                     result IS NOT NULL AS has_result FROM research_jobs WHERE id=?''',(job_id,)).fetchone()
    if row is None:raise KeyError(job_id)
    return dict(row)


def _state(value,header):
    if not isinstance(value,dict):return (0,0,0,0,False,True)
    source=value.get('source_text');target=value.get('translated_text');box=value.get('bbox');page=value.get('page')
    valid=(isinstance(value.get('block_id'),str) and bool(value['block_id']) and
           isinstance(page,int) and not isinstance(page,bool) and page>=1 and isinstance(box,list) and len(box)==4 and
           all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0<=v<=1 for v in box) and
           box[0]<box[2] and box[1]<box[3] and bool(header.get('document_id')) and bool(header.get('run_id')) and
           value.get('document_id')==header['document_id'] and value.get('run_id')==header['run_id'])
    return (len(source) if isinstance(source,str) else 0,len(target) if isinstance(target,str) else 0,
            source.count('\n') if isinstance(source,str) else 0,target.count('\n') if isinstance(target,str) else 0,
            bool(valid),not isinstance(target,str) or not target.strip())


def ensure_index(db,job):
    if job['kind']!='translate':raise ValueError('此任务不是文献分段翻译。')
    job_id=job['id'];meta=db.execute('SELECT * FROM translation_page_meta WHERE job_id=?',(job_id,)).fetchone()
    if meta is not None and not meta['dirty'] and meta['index_version']==INDEX_VERSION:return dict(meta)
    if meta is not None and meta['index_version']!=INDEX_VERSION:
        db.execute('UPDATE translation_page_meta SET dirty=1,revision=revision+1 WHERE job_id=?',(job_id,))
    db.execute('INSERT OR IGNORE INTO translation_page_meta(job_id) VALUES(?)',(job_id,))
    payload=json.loads(job['payload']);source_kind='items';error=None
    header={k:payload[k] for k in ('document_id','run_id','target_language') if k in payload}
    header.update(status='partial',total_blocks=job['total'],scope_metadata_pending=True)
    if job['has_result']:
        source_kind='result'
        well_formed=db.execute('SELECT json_valid(result) FROM research_jobs WHERE id=?',(job_id,)).fetchone()[0]
        kind=db.execute("SELECT json_type(result,'$.blocks') FROM research_jobs WHERE id=?",(job_id,)).fetchone()[0] if well_formed else None
        if kind!='array':error='保存结果没有分段数组，可通过完整导出核对原始记录。'
        if not well_formed:error='保存结果包含非标准JSON，分页不可用；原记录未改写。'
        metadata=db.execute("SELECT json_remove(result,'$.blocks') FROM research_jobs WHERE id=?",(job_id,)).fetchone()[0] if well_formed else '{}'
        decoded=json.loads(metadata)
        if isinstance(decoded,dict):
            allowed={'document_id','run_id','target_language','scope','status','total_blocks','total_pages','parsed_pages','missing_pages','glossary'}
            header={k:v for k,v in decoded.items() if k in allowed}
        # json_each returns each object without decoding the entire result in Python.
        rows=db.execute('''SELECT CASE type WHEN 'text' THEN json_quote(value) WHEN 'true' THEN 'true'
                           WHEN 'false' THEN 'false' WHEN 'null' THEN 'null' ELSE value END
                           FROM json_each((SELECT result FROM research_jobs WHERE id=?),'$.blocks')
                           ORDER BY CAST(key AS INTEGER)''',(job_id,)) if not error else []
    else:
        rows=db.execute('''SELECT payload FROM research_job_items WHERE job_id=?
             AND item_key NOT IN ('review_validation','grounding_validation') AND item_key NOT LIKE 'diagnostic:%'
             ORDER BY rowid''',(job_id,))
    db.execute('DELETE FROM translation_page_blocks WHERE job_id=?',(job_id,))
    total=0
    for ordinal,row in enumerate(rows):
        raw=row[0]
        if not isinstance(raw,str):raw=json.dumps(raw,ensure_ascii=False)
        value=json.loads(raw)
        try:json.dumps(value,allow_nan=False)
        except ValueError:error='保存块包含非有限JSON值，可通过完整导出核对原始记录。'
        block_id=value.get('block_id') if isinstance(value,dict) and isinstance(value.get('block_id'),str) else None
        db.execute('INSERT INTO translation_page_blocks VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (job_id,ordinal,block_id,raw,*_state(value,header)))
        total+=1
    db.execute('UPDATE translation_page_meta SET dirty=0,source_kind=?,total=?,header=?,error=?,index_version=? WHERE job_id=?',
               (source_kind,total,json.dumps(header,ensure_ascii=False),error,INDEX_VERSION,job_id))
    return dict(db.execute('SELECT * FROM translation_page_meta WHERE job_id=?',(job_id,)).fetchone())


def summary(db,job):
    meta=ensure_index(db,job);job_id=job['id']
    invalid,missing=db.execute('SELECT COALESCE(SUM(NOT anchor_valid),0),COALESCE(SUM(missing_translation),0) FROM translation_page_blocks WHERE job_id=?',(job_id,)).fetchone()
    duplicates=[dict(r) for r in db.execute('''SELECT block_id,COUNT(*) AS count FROM translation_page_blocks
                   WHERE job_id=? AND block_id IS NOT NULL GROUP BY block_id HAVING COUNT(*)>1''',(job_id,))]
    return dict(total=meta['total'],revision=meta['revision'],result_metadata=json.loads(meta['header']),
                source_kind=meta['source_kind'],invalid_block_count=invalid,missing_translation_count=missing,
                duplicate_id_count=len(duplicates),ambiguous_anchor_count=sum(r['count'] for r in duplicates),
                duplicate_block_ids=[r['block_id'] for r in duplicates[:20]],
                index_error=meta['error'],index_version=meta['index_version'],paging_supported=True)


def page(library,job_id,*,offset=0,limit=100,revision=None,block_id=None,include_index=False):
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0 or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:
        raise ValueError('offset须为非负整数，limit须为1至200。')
    with library._connection() as db:
        db.execute('BEGIN IMMEDIATE')
        job=header_row(db,job_id);info=summary(db,job)
        if info['index_error']:raise ValueError(info['index_error'])
        if revision is not None and revision!=info['revision']:raise TranslationRevisionChanged('译文保存版本已变化，请重新读取摘要。')
        if block_id is not None:
            matches=db.execute('SELECT ordinal FROM translation_page_blocks WHERE job_id=? AND block_id=?',(job_id,block_id)).fetchall()
            if not matches:raise KeyError(block_id)
            if len(matches)!=1:raise ValueError('原文块标识重复，不能唯一定位。')
            offset=matches[0][0]
        selected=db.execute('''SELECT * FROM translation_page_blocks WHERE job_id=? AND ordinal>=?
                             ORDER BY ordinal LIMIT ?''',(job_id,offset,limit)).fetchall()
        duplicate_ids={r[0] for r in db.execute('''SELECT block_id FROM translation_page_blocks WHERE job_id=?
                      AND block_id IS NOT NULL GROUP BY block_id HAVING COUNT(*)>1''',(job_id,))}
        blocks=[json.loads(row['payload']) for row in selected]
        states=[dict(ordinal=row['ordinal'],anchor_valid=bool(row['anchor_valid']) and row['block_id'] not in duplicate_ids,
                     duplicate_block_id=row['block_id'] in duplicate_ids,missing_translation=bool(row['missing_translation'])) for row in selected]
        value=dict(info,job_id=job_id,status=job['status'],offset=offset,limit=limit,blocks=blocks,record_metadata=states,
                   next_offset=offset+len(blocks) if offset+len(blocks)<info['total'] else None)
        if include_index:
            value['index']=[dict(row) for row in db.execute('''SELECT ordinal,block_id,source_chars,translation_chars,
                        source_newlines,translation_newlines FROM translation_page_blocks WHERE job_id=? ORDER BY ordinal''',(job_id,))]
        return value
