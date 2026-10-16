"""Explicit model acquisition; runtime inference itself always stays offline."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_workspace.semantic import MODEL,MANIFEST


def main():
    cache=ROOT/'data/models';cache.mkdir(parents=True,exist_ok=True)
    if MANIFEST.exists():raise SystemExit('Model manifest already exists; preserve current model and verify it instead.')
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    os.environ['HF_HOME']=str(cache/'huggingface')
    os.environ['HF_XET_CACHE']=str(cache/'huggingface/xet')
    from fastembed import TextEmbedding
    model=TextEmbedding(model_name=MODEL,cache_dir=str(cache),threads=2,providers=['CPUExecutionProvider'])
    import numpy as np
    probe=list(model.embed(['A retriever finds relevant passages for a question.']))[0]
    assert len(probe)==384 and np.isfinite(probe).all()
    directory=Path(model.model._model_dir).resolve()
    assert directory.is_relative_to(cache.resolve())
    files=[{'path':str(p.relative_to(directory)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(directory.rglob('*')) if p.is_file()]
    assert files
    data={'model':MODEL,'dimensions':384,'language':'English','model_directory':str(directory.relative_to(ROOT)),
        'license':'MIT','source':'https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q','files':files}
    data['fingerprint']=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    with MANIFEST.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':'prepared_and_inferred','manifest':str(MANIFEST),'model':MODEL,
        'dimensions':384,'model_bytes':sum(f['bytes'] for f in files),'fingerprint':data['fingerprint']},ensure_ascii=False))


if __name__=='__main__':main()
