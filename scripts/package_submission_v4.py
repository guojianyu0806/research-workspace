"""Assemble the selected real-UI v5 and a frozen local source/history package.

This tool performs no network, deployment, installation or model requests. It
never updates prior deliveries. All staging and restoration paths are new,
project-local directories. Run only after committing the reviewed materials.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'artifacts/submission-pack-v4'
VIDEO_ROOT = ROOT / 'artifacts/video-real-ui-20261030-v5'
VIDEO = VIDEO_ROOT / 'final-v5-r2/研序_真实页面演示_剪映正常配音_v5.mp4'
VIDEO_SHA = '869e3335c8f5144eba8a2854827d75051a586f42a84d8b51dc9069b884d72dd2'
DEFAULT_OUTPUT = ROOT / 'deliverables/郭剑宇_研序_提交材料_20261031_v4'
HISTORICAL = ROOT / 'artifacts/regression-local/20261027T081810Z-c4f62f'
TASK_IDS = ['eaefb9350c8641f28918fa841a693414', '697975c3f7d24a1db889c8ebbcf5549d',
            '182925d4ab6a4a40a94a45561b076e83', '030604c6c0b14ad682ec934d52561546']
FORBIDDEN_ROOTS = {'.git', '.venv', '.runtime', 'artifacts', 'deliverables', 'data', 'node_modules'}
SECRET_PATTERNS = {
    'provider_token': rb'\bsk-[A-Za-z0-9_-]{20,}',
    'github_token': rb'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}',
    'hf_token': rb'\bhf_[A-Za-z0-9]{24,}',
    'private_key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
}
SAFE_MARKERS = ('fixture', 'not-a-real', 'not_real', 'placeholder', 'example', 'redacted', 'dummy', 'offline', 'test-only')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, check=check,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def git_text(*args: str, cwd: Path = ROOT) -> str:
    return git(*args, cwd=cwd).stdout.decode('utf-8', 'replace').strip()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8-sig', newline='\n') as stream:
        stream.write(text)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def safe_path(path: str) -> None:
    item = PurePosixPath(path)
    if item.is_absolute() or '..' in item.parts or not item.parts or '\\' in path:
        raise ValueError(f'Unsafe archive member path: {path}')
    if item.parts[0].lower() in FORBIDDEN_ROOTS:
        raise ValueError(f'Excluded source root unexpectedly tracked: {item.parts[0]}')
    name = item.name.lower()
    if name == '.env' or (name.startswith('.env.') and name != '.env.example'):
        raise ValueError('Real environment configuration must not be archived')
    if name.endswith(('.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.pem', '.pfx', '.key')):
        raise ValueError(f'Excluded private/runtime file type: {item.suffix}')


def screen_bytes(data: bytes, label: str) -> list[dict]:
    explicit_fixtures = []
    for kind, pattern in SECRET_PATTERNS.items():
        for match in re.finditer(pattern, data):
            value = match.group().decode('utf-8', 'ignore').lower()
            finding = {'path': label, 'kind': kind,
                       'match_sha256': hashlib.sha256(match.group()).hexdigest()}
            if any(marker in value for marker in SAFE_MARKERS):
                explicit_fixtures.append(finding)
            else:
                # Never include a matching value in an exception or report.
                raise ValueError(f'Unresolved secret pattern in {label}; kind={kind}; value omitted')
    return explicit_fixtures


def sanitize(value):
    if isinstance(value, dict):
        return {sanitize(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        for source, token in [(ROOT, '${PROJECT_ROOT}'), (Path.home(), '${USER_HOME}')]:
            value = value.replace(str(source).replace('\\', '\\\\'), token)
            value = value.replace(str(source), token).replace(source.as_posix(), token)
        return value
    return value


def tree_blob_hashes(repo: Path, revision: str) -> dict:
    records = []
    for entry in git('ls-tree', '-r', '-z', '--full-tree', revision, cwd=repo).stdout.split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode('ascii').split()
        if kind != 'blob' or mode == '120000':
            raise ValueError('This delivery verifier expects ordinary tracked files, not symlinks/submodules')
        records.append((name.decode('utf-8'), oid, mode))
    oids = list(dict.fromkeys(oid for _, oid, _ in records))
    raw = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo,
                         input=('\n'.join(oids)+'\n').encode('ascii'), capture_output=True,
                         check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    hashes, pos = {}, 0
    for expected_oid in oids:
        end = raw.index(b'\n', pos)
        oid, kind, size = raw[pos:end].decode('ascii').split()
        size = int(size)
        if oid != expected_oid or kind != 'blob':
            raise ValueError('Unexpected restored Git object')
        hashes[oid] = hashlib.sha256(raw[end+1:end+1+size]).hexdigest()
        pos = end+2+size
    return {name: {'git_blob': oid, 'git_mode': mode, 'sha256': hashes[oid]} for name, oid, mode in records}


def auxiliary_ref_coverage(revision: str) -> list[dict]:
    reachable = {line.split(b' ', 1)[0].decode('ascii')
                 for line in git('rev-list', '--objects', revision).stdout.splitlines()}
    records = []
    for line in git_text('for-each-ref', '--format=%(refname)%09%(objectname)%09%(objecttype)').splitlines():
        name, oid, kind = line.split('\t')
        if kind != 'commit':
            covered = oid in reachable
            records.append({'ref': name, 'object': oid, 'object_type': kind,
                            'covered_by_head_object_scan': covered, 'counted_as_development_commit': False})
            if not covered:
                raise ValueError(f'Auxiliary {kind} ref is outside the scanned HEAD object set; extend the scan before exporting: {name}')
    return records


def prompt_records(revision: str, files: list[dict]) -> dict:
    records = []
    for item in files:
        if not item['path'].startswith('research_workspace/prompts/'):
            continue
        payload = git('cat-file', 'blob', item['git_blob']).stdout
        header = next((line.strip() for line in payload.decode('utf-8-sig').splitlines()[:5]
                       if line.lower().startswith('prompt version:')), None)
        records.append({**item, 'version_header': header,
                        'actual_source_in_package': 'project/'+item['path']})
    return {'source_revision': revision, 'template_count': len(records), 'templates': records,
            'scope': 'Versioned runtime templates present at this frozen commit; historical alternatives are retained. Presence does not establish that every template ran in every demo task.',
            'private_chat_included': False,
            'execution_evidence': 'See the four task-specific checks in acceptance/本轮Flash演示; exact source template files are in project/research_workspace/prompts/.',
            'current_principal_examples': ['qa_v5.txt', 'grounding_v3.txt', 'retrieval_query_v2.txt']}


def unique_file(directory: Path, suffix: str, keyword: str) -> Path:
    matches = [p for p in directory.glob('*' + suffix) if keyword in p.stem]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {keyword}{suffix} in the reviewed docs directory')
    return matches[0]


def copy_new(source: Path, destination: Path) -> None:
    if not source.is_file() or destination.exists():
        raise ValueError(f'Missing source or existing destination: {destination.name}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha(source) != sha(destination):
        raise ValueError(f'Copy hash mismatch: {destination.name}')


def collect_evidence(destination: Path, source_records: list[dict]) -> None:
    sources = [
        (HISTORICAL/'summary.json', '历史全量回归_20261027/summary.json'),
        (HISTORICAL/'manifest.json', '历史全量回归_20261027/manifest.json'),
        (HISTORICAL/'junit.xml', '历史全量回归_20261027/junit.xml'),
        (VIDEO_ROOT/'final-v5-r2/delivery-status.json', '选定v5视频/delivery-status.json'),
        (VIDEO_ROOT/'final-v5-r2/final-checks.json', '选定v5视频/final-checks.json'),
        (VIDEO_ROOT/'final-v5-r2/media-info.json', '选定v5视频/media-info.json'),
        (VIDEO_ROOT/'review-final-v1/visual-review-result.json', '选定v5视频/visual-review-result.json'),
        (VIDEO_ROOT/'audio-final-review-v1/audio-final-signal-checks-v1.json', '选定v5视频/audio-final-signal-checks-v1.json'),
        (VIDEO_ROOT/'export-evidence-v1/research-030604c6c0b14ad682ec934d52561546-export-checks.json', '本轮Flash演示/框架导出核对.json'),
        (VIDEO_ROOT/'export-evidence-v1/chart-60e3ef7a3b854aa49ee9a455dd7d1401/chart-export-checks.json', '本轮Flash演示/图表导出核对.json'),
    ]
    sources.extend((VIDEO_ROOT/f'live-task-evidence-v1/{task}-checks.json', f'本轮Flash演示/{task}-checks.json') for task in TASK_IDS)
    for source, name in sources:
        if not source.is_file():
            raise FileNotFoundError(f'Expected reviewed evidence: {source.relative_to(ROOT)}')
        target = destination/name
        raw = source.read_bytes()
        screen_bytes(raw, source.relative_to(ROOT).as_posix())
        if source.suffix == '.json':
            write_json(target, sanitize(json.loads(raw.decode('utf-8-sig'))))
        elif source.suffix == '.xml':
            write_text(target, sanitize(raw.decode('utf-8-sig')))
        else:
            copy_new(source, target)
        source_records.append({'source': source.relative_to(ROOT).as_posix(),
                               'source_sha256': sha(source), 'staged_relative_path': name,
                               'staged_sha256': sha(target),
                               'redaction': 'Absolute project/home paths replaced with placeholders; factual values retained.'})


def verify_bundle_and_sources(archive: Path, bundle: Path, revision: str, work: Path) -> dict:
    verified = git('bundle', 'verify', str(bundle))
    clone_dir = work/'restored-repository'
    git('clone', str(bundle), str(clone_dir))
    restored_head = git_text('rev-parse', 'HEAD', cwd=clone_dir)
    if restored_head != revision:
        raise ValueError('Restored bundle HEAD differs from the frozen source revision')
    fsck = git('fsck', '--full', cwd=clone_dir)
    original_count = int(git_text('rev-list', '--all', '--count'))
    restored_count = int(git_text('rev-list', '--all', '--count', cwd=clone_dir))
    if restored_count != original_count:
        raise ValueError('Restored all-ref commit count differs from the source repository')
    refs = git_text('bundle', 'list-heads', str(bundle))
    bundled_refs = dict(line.split(' ', 1)[::-1] for line in refs.splitlines())
    if bundled_refs.get('refs/heads/main') != revision:
        raise ValueError('Bundle must preserve the complete frozen main development history')
    inventory = []
    for line in git_text('for-each-ref', '--format=%(refname)%09%(objectname)%09%(objecttype)').splitlines():
        name, oid, kind = line.split('\t')
        inventory.append({'ref': name, 'object': oid, 'object_type': kind,
                          'development_commit_ref': kind == 'commit'})
    covered_auxiliary_refs = auxiliary_ref_coverage(revision)
    restored_tree = tree_blob_hashes(clone_dir, revision)
    source_manifest = []
    fixtures = []
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise ValueError('Git archive CRC verification failed')
        for info in package.infolist():
            if info.is_dir():
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Symlink archive member requires a separate explicit delivery policy')
            safe_path(info.filename)
            data = package.read(info)
            fixtures.extend(screen_bytes(data, info.filename))
            restored = restored_tree.get(info.filename)
            if restored is None or hashlib.sha256(data).hexdigest() != restored['sha256']:
                raise ValueError(f'Archive/restored-file mismatch: {info.filename}')
            source_manifest.append({'path': info.filename, 'bytes': len(data),
                                    **restored})
    tree_count = len([name for name in git('ls-tree', '-r', '--name-only', '-z', revision).stdout.split(b'\0') if name])
    if tree_count != len(source_manifest):
        raise ValueError('Source archive file count differs from the frozen Git tree')
    historical_manifest = json.loads((HISTORICAL/'manifest.json').read_text(encoding='utf-8'))
    changed_historical_sources = [path for path, digest in historical_manifest['source_sha256'].items()
                                  if sha(ROOT/path) != digest]
    if changed_historical_sources:
        raise ValueError('Application/test files changed relative to the historical regression; revise its stated evidence boundary')
    return {'revision': revision, 'restore_head': restored_head, 'bundle_verify_passed': True,
            'bundle_verify_output': sanitize((verified.stdout+verified.stderr).decode('utf-8', 'replace')),
            'git_fsck_passed': True, 'git_fsck_output': sanitize((fsck.stdout+fsck.stderr).decode('utf-8', 'replace')),
            'all_ref_commit_count': original_count, 'restored_all_ref_commit_count': restored_count,
            'head_commit_count': int(git_text('rev-list', '--count', revision)),
            'bundle_heads': refs, 'source_ref_inventory': inventory,
            'noncommit_auxiliary_refs': [r for r in inventory if r['object_type'] != 'commit'],
            'auxiliary_ref_scan_coverage': covered_auxiliary_refs,
            'noncommit_ref_policy': 'All refs are preserved in the bundle; auxiliary tree refs are not development commits and are not counted as such. Restoration compares main history, not retention of temporary Codex tree refs.',
            'source_archive_crc_passed': True,
            'source_archive_files': len(source_manifest), 'all_archived_files_match_restored_tree': True,
            'source_files': source_manifest, 'explicit_fixture_pattern_matches': fixtures,
            'source_hash_scope': 'Raw Git blobs in archive and restored repository; checkout may legitimately apply .gitattributes line endings.',
            'historical_regression_date': '2026-10-27', 'historical_tests': 1277,
            'historical_test_revision': historical_manifest['source_revision'],
            'historical_hashed_files_checked': len(historical_manifest['source_sha256']),
            'current_historical_hashes_match': True, 'new_full_regression_run': False,
            'restore_scope': 'Git/source integrity restoration only; no new dependency install, application/model run or cross-machine acceptance.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs-dir', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--secret-scan', required=True, type=Path)
    parser.add_argument('--document-checks', type=Path)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    docs = args.docs_dir.resolve()
    output = args.output_dir.resolve()
    scan_path = args.secret_scan.resolve()
    revision = git_text('rev-parse', '--verify', args.revision+'^{commit}')
    if git_text('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Commit the reviewed source and documentation before freezing this package')
    if revision != git_text('rev-parse', 'HEAD'):
        raise ValueError('The package revision must equal current HEAD')
    if not output.is_relative_to(ROOT/'deliverables') or output.exists():
        raise ValueError('Choose a new delivery folder inside this project; prior material is never overwritten')
    if not docs.is_relative_to(CASE) or not docs.is_dir() or not scan_path.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use reviewed project-local docs and scan paths')
    scan = json.loads(scan_path.read_text(encoding='utf-8-sig'))
    if scan.get('revision') != revision or not scan.get('passed') or scan.get('unresolved'):
        raise ValueError('Need a passing, unresolved-free source/history scan at the exact frozen revision')
    if git_text('rev-list', '--all', '--not', revision):
        raise ValueError('Some exported refs are outside the scanned HEAD history; extend the history scan before packaging')
    auxiliary_ref_coverage(revision)
    if sha(VIDEO) != VIDEO_SHA:
        raise ValueError('The approved v5 video changed; refuse to substitute another video')
    technical_pdf = unique_file(docs, '.pdf', '技术文档')
    statement_pdf = unique_file(docs, '.pdf', 'AI创作说明')
    for pdf in [technical_pdf, statement_pdf]:
        if not pdf.read_bytes().startswith(b'%PDF-'):
            raise ValueError('Expected a real exported PDF')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work = CASE/f'package-verification-{stamp}-{revision[:7]}'
    work.mkdir(parents=True, exist_ok=False)
    archive = work/'frozen-source.zip'
    bundle = work/'research-workspace-history.bundle'
    git('archive', '--format=zip', '--output='+str(archive), revision)
    git('bundle', 'create', str(bundle), '--all')
    integrity = verify_bundle_and_sources(archive, bundle, revision, work)
    output.mkdir(parents=True, exist_ok=False)
    upload = output/'01_上传百度网盘_作品文件'
    single = output/'02_表单第6项_只上传一个PDF'
    editable = output/'03_可编辑文档与字幕_无需重复上传'
    evidence = output/'04_校验与来源_源码包内亦有摘要'
    for directory in [upload, single, editable, evidence]:
        directory.mkdir(exist_ok=False)
    copy_new(technical_pdf, upload/'01_研序_技术文档_郭剑宇_20261031_v3.pdf')
    copy_new(VIDEO, upload/'02_研序_真实页面演示_剪映正常配音_v5.mp4')
    copy_new(statement_pdf, upload/'04_AI创作说明_郭剑宇_v2.pdf')
    copy_new(statement_pdf, single/'研序_AI创作说明_郭剑宇_v2.pdf')
    for path in sorted(docs.iterdir()):
        if path.is_file() and path.suffix.lower() in {'.docx', '.md', '.txt', '.png'}:
            copy_new(path, editable/path.name)
    copy_new(VIDEO_ROOT/'final-v5-r2/研序_真实页面演示_v5.srt', editable/'研序_真实页面演示_v5.srt')
    evidence_sources = []
    collect_evidence(evidence, evidence_sources)
    write_json(evidence/'最新源码与历史恢复检查.json', sanitize(integrity))
    write_json(evidence/'实际Prompt版本与源码清单.json', prompt_records(revision, integrity['source_files']))
    write_text(evidence/'实际Prompt说明.txt',
        '实际运行模板由project/research_workspace/prompts/的版本化源码提供；版本、Git blob和SHA-256见实际Prompt版本与源码清单.json。\n'
        '主要例子包括qa_v5.txt的逐结论引句选择、grounding_v3.txt的当前claim_index支持审核及retrieval_query_v2.txt的检索维度规划。\n'
        '目录保留历史模板以核对演进；文件存在不表示每个模板都在本轮四任务中执行。任务事实与来源核对另见本轮Flash演示。\n'
        '这些是源码模板与专业整理说明，不是完整私人聊天或模型隐藏推理；未编造执行记录。\n')
    write_json(evidence/'源码与历史密钥模式检查.json', sanitize(scan))
    write_json(evidence/'证据来源与路径脱敏映射.json', evidence_sources)
    for name in ['build-info.json', 'document-validation.json', 'render-checks.json']:
        source = docs/name
        if source.is_file():
            write_json(evidence/('文档_'+name), sanitize(json.loads(source.read_text(encoding='utf-8-sig'))))
    if args.document_checks:
        source = args.document_checks.resolve()
        if not source.is_relative_to(CASE) or not source.is_file():
            raise ValueError('Document checks must be an existing artifact in the package case directory')
        write_json(evidence/'文档独立校验.json', sanitize(json.loads(source.read_text(encoding='utf-8-sig'))))
    write_text(evidence/'验证范围说明.txt',
        '本目录为白名单交付证据，另随源码ZIP的acceptance目录提供。\n'
        '1277项为2026年10月27日历史全量本地回归，本次仅核对其179份应用/测试文件摘要，不冒充新全量测试。\n'
        'v5的四场普通Flash演示共35次请求，均来自实际任务；引用、审核、翻译来源及下载另有检查。翻译不具备逐事实语义审核。\n'
        '原声信号匹配、视频完整解码与128样点视觉检查，不等同完整人工逐句试听或逐帧观看。\n'
        'Pro付费复测已取消；未验收部分保留边界。官方资料包、复杂解析、跨语言、外部长期效果等不冒充已通过。\n'
        'Git/source恢复不包含安装依赖、加载私库、模型调用或异机部署。项目和用户目录已替换为占位路径，事实与摘要未改。\n'
        '不包含原生剪映私人项目索引、原始录屏、完整私聊、jobs原件、真实环境配置、私库、模型权重或虚拟环境。\n')
    missing = ('外部提交条件 仍待完成\n\n'
               'GitHub/Gitee评审访问仓库：待定，本次没有推送。\n'
               '公网演示URL与评审专用账号：待部署，本次没有公网部署或容器构建验收。\n'
               '报名组别、报名号等字段：按本人个人中心填写；郭剑宇个人参赛，无指导老师。\n'
               '百度网盘分享链接与提取码：上传完成后自行生成并回填，建议长期有效。\n'
               '本次仅完成本地材料整理，不等同网盘上传、评审授权、部署、表单提交或比赛验收通过。\n'
               'Pro付费复测不再开展；当前未验收范围如实保留，不填造通过结果。\n')
    write_text(upload/'05_仓库公网与报名信息_待补.txt', missing)
    write_text(upload/'00_作品阅读与运行说明.txt',
        '研序 可溯源科研助手 Agent\n参赛者：郭剑宇；个人参赛；指导老师：无。\n\n'
        '01为新版技术PDF；02为已选定的v5视频（5分34秒，1920×1080，普通话，剪映解说小帅原生正常1倍速）；'
        '03为最新源码、完整真实Git历史与必要证据；04为300字以内AI创作说明；05明确尚待完成的外部条件。\n'
        f'本包冻结源码提交：{revision}\n选定视频SHA-256：{VIDEO_SHA}\n\n'
        '解压03后先读history/README_恢复与运行.txt，源码在project/，Git bundle在history/。'
        '在项目专用虚拟环境按project/README.md或README_SUBMISSION.md安装运行；真实密钥自行在本机配置，不随包提供。\n'
        '源码包不带虚拟环境、数据库、原论文、OCR数据和向量模型权重；按README及版本清单另行准备。运行期间不隐式下载。\n'
        '本轮没有安装、运行模型、重新执行全量软件回归、推送仓库或部署公网；源码恢复与历史完整性已单独验证。'
        '验收证据见源码ZIP acceptance/，历史测试与本轮Flash演示的日期、版本和范围分开记录。\n'
        '如官方仍要求GitHub/Gitee与公网演示，本地ZIP或本地地址不能代替这些条件。\n')
    history_dir = work/'history'
    history_dir.mkdir(exist_ok=False)
    copy_new(bundle, history_dir/bundle.name)
    write_text(history_dir/'git-log.txt', git_text('log', '--all', '--date=iso-strict', '--format=fuller', '--date-order')+'\n')
    write_text(history_dir/'bundle-heads.txt', integrity['bundle_heads']+'\n')
    write_json(history_dir/'source-file-hashes.json', {'revision': revision, 'files': integrity['source_files']})
    write_json(history_dir/'restore-verification.json', sanitize(integrity))
    write_text(history_dir/'README_恢复与运行.txt',
        '本文件夹保留真实Git bundle和开发历史，不回溯伪造日期、不压缩历史。\n'
        f'冻结HEAD：{revision}\n全部refs可达提交数：{integrity["all_ref_commit_count"]}\n\n'
        '在一个新的空目标路径恢复：\n'
        'git clone history/research-workspace-history.bundle restored-project\n'
        'git -C restored-project rev-parse HEAD\n'
        'git -C restored-project fsck --full\n\n'
        '命令从本ZIP解压根执行，目标restored-project必须尚未存在。恢复后按README运行。'
        '本地已在独立新目录clone、核对HEAD、全部refs提交数、git fsck及逐源码文件摘要。\n'
        'project目录来自git archive同一冻结提交，不含.git；history/bundle是完整历史，git-log只是辅助，不能替代真实历史。'
        'bundle不是在线GitHub/Gitee评审访问地址。acceptance提供必要的历史测试与本轮演示核对，不包含私库/密钥/原始私聊。\n')
    source_zip = upload/f'03_研序_源码与完整Git历史_{revision[:7]}.zip'
    with zipfile.ZipFile(source_zip, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        with zipfile.ZipFile(archive) as original:
            for info in original.infolist():
                if not info.is_dir():
                    target.writestr('project/'+info.filename, original.read(info))
        for prefix, directory in [('history', history_dir), ('acceptance', evidence)]:
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    target.write(path, prefix+'/'+path.relative_to(directory).as_posix())
    with zipfile.ZipFile(source_zip) as package:
        bad = package.testzip()
        if bad:
            raise ValueError(f'Final source/history ZIP CRC failed for {bad}')
        project_files = [name for name in package.namelist() if name.startswith('project/')]
        if len(project_files) != integrity['source_archive_files']:
            raise ValueError('Packaged project file count differs from verified Git archive')
        for entry in integrity['source_files']:
            if hashlib.sha256(package.read('project/'+entry['path'])).hexdigest() != entry['sha256']:
                raise ValueError('Final ZIP changed an archived source blob')
        if hashlib.sha256(package.read('history/'+bundle.name)).hexdigest() != sha(bundle):
            raise ValueError('Final ZIP changed the verified Git bundle')
    upload_files = [{'path': p.relative_to(upload).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                    for p in sorted(upload.rglob('*')) if p.is_file()]
    upload_manifest = upload/'提交文件校验清单.json'
    write_json(upload_manifest, {'source_revision': revision, 'selected_video_version': 'v5 / final-v5-r2',
               'selected_video_sha256': VIDEO_SHA, 'uploaded': False, 'deployed': False, 'submitted': False,
               'scope': 'Files intended for Baidu upload; excludes this manifest and SHA256SUMS.txt to avoid self-reference.',
               'checksum_rule': 'SHA256SUMS.txt includes all listed files plus this manifest and excludes itself.',
               'files': upload_files})
    write_text(upload/'SHA256SUMS.txt', ''.join(f"{item['sha256']}  {item['path']}\n" for item in upload_files)
               + f'{sha(upload_manifest)}  {upload_manifest.name}\n')
    write_text(output/'00_先读_上传哪个文件夹.txt',
        '本地提交材料 v4  2026年10月31日\n\n'
        '上传百度网盘：上传01_上传百度网盘_作品文件整个文件夹，其中包含已选v5视频、新版技术PDF、最新源码与完整Git历史、AI短说明及阅读说明。'
        '必要验收摘要已纳入源码ZIP acceptance/，无需把原始运行数据或本地录屏另行上传。\n'
        '表单第6项：只上传02_表单第6项_只上传一个PDF中的那一份PDF。网盘分享链接填写第5项，建议长期有效并附提取码。\n'
        '03为可编辑文档与字幕备份；04为便于本地核对的证据展开件，均无需重复上传。'
        '01内自带提交文件校验清单和SHA256SUMS，随作品上传后可核对；根目录另有覆盖本地备份和证据的完整校验清单。\n\n'
        f'源码冻结：{revision}\n视频：v5 / final-v5-r2；5分34秒；正常1倍速；不含说明卡片或解释动画。\n'
        '旧文件夹、旧视频、旧PDF、旧工程和旧压缩包全部保留；本目录全新创建，没有覆盖旧成品。'
        '本轮没有上传网盘、推送仓库、部署公网或正式提交比赛。仓库URL、公网URL/账号及报名待填项另见01中的05说明。\n')
    payload = []
    for path in sorted(output.rglob('*')):
        if path.is_file():
            payload.append({'path': path.relative_to(output).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    manifest = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'material_date': '2026-10-31',
                'status': 'Local updated materials assembled; not uploaded, pushed, deployed or formally submitted',
                'source_revision': revision, 'source_head_commit_count': integrity['head_commit_count'],
                'source_all_ref_commit_count': integrity['all_ref_commit_count'],
                'source_archive_tree_and_restore_verified': True, 'source_zip_crc_passed': True,
                'git_bundle_verify_passed': True, 'git_fsck_passed': True,
                'source_zip': source_zip.relative_to(output).as_posix(), 'selected_video_version': 'v5 / final-v5-r2',
                'selected_video_sha256': VIDEO_SHA, 'video_copy_byte_identical': sha(upload/'02_研序_真实页面演示_剪映正常配音_v5.mp4') == VIDEO_SHA,
                'old_artifacts_preserved': True, 'overwritten_old_finished_files': [],
                'new_model_calls': 0, 'new_pro_calls': 0, 'pro_retest_status': 'Canceled by user; unaccepted scope retained',
                'new_full_regression_run': False, 'historical_regression_date': '2026-10-27', 'historical_tests': 1277,
                'evidence_absolute_paths_redacted': True, 'uploaded': False, 'pushed': False, 'deployed': False, 'submitted': False,
                'pending_external_items': ['GitHub/Gitee evaluator-access repository URL', 'Public demo URL and necessary evaluator account',
                                           'Baidu share URL/extraction code after user upload', 'Actual registration fields and official submission'],
                'checksum_rule': 'files lists every payload file; excludes this manifest and SHA256SUMS.txt to avoid self-reference. SHA256SUMS.txt covers every payload file plus this manifest, excluding only itself.',
                'files': payload}
    manifest_path = output/'材料校验清单.json'
    write_json(manifest_path, manifest)
    write_text(output/'SHA256SUMS.txt', ''.join(f"{item['sha256']}  {item['path']}\n" for item in payload) + f'{sha(manifest_path)}  材料校验清单.json\n')
    for item in payload:
        if sha(output/item['path']) != item['sha256']:
            raise ValueError('Final staged file hash check failed')
    write_json(work/'package-result.json', {'output': str(output.relative_to(ROOT)), 'revision': revision,
               'manifest_sha256': sha(manifest_path), 'checksums_sha256': sha(output/'SHA256SUMS.txt'),
               'payload_files': len(payload), 'source_zip_sha256': sha(source_zip),
               'source_zip_crc_passed': True, 'restore_passed': True, 'all_payload_hashes_passed': True})
    print(json.dumps({'output': str(output), 'revision': revision, 'files': len(payload),
                      'source_zip': source_zip.name, 'source_files': integrity['source_archive_files'],
                      'all_ref_commits': integrity['all_ref_commit_count'], 'crc': True, 'restore': True,
                      'video_sha256': VIDEO_SHA, 'old_artifacts_preserved': True, 'uploaded': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
