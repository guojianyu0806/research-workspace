"""Package current form 5/6/7 materials in a fresh local delivery folder.

Uses the verified v4 Git/blob helpers. No publication, installation, network or
model call occurs. Prior finished files and all prior deliveries are preserved.
"""
from __future__ import annotations

import argparse
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

import package_submission_v4 as base

ROOT = base.ROOT
CASE = ROOT / 'artifacts/submission-pack-v5'
DEFAULT = ROOT / 'deliverables/郭剑宇_研序_正式提交整理_20261031_v5'
VIDEO_SHA = base.VIDEO_SHA


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def verify_old_files():
    baseline = read_json(CASE / 'old-artifact-baseline-v1.json')
    for record in baseline['files']:
        path = ROOT / record['path']
        if not path.is_file() or base.sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
            raise ValueError('Prior finished file changed: ' + record['path'])
    return {'passed': True, 'checked_files': len(baseline['files']),
            'baseline_sha256': base.sha(CASE / 'old-artifact-baseline-v1.json'),
            'old_finished_files_overwritten': []}


def checksums(directory, name, metadata):
    records = [{'path': p.relative_to(directory).as_posix(), 'bytes': p.stat().st_size, 'sha256': base.sha(p)}
               for p in sorted(directory.rglob('*')) if p.is_file()]
    manifest = directory / name
    base.write_json(manifest, {**metadata,
        'checksum_rule': 'files excludes this manifest and SHA256SUMS.txt; SHA256SUMS covers all listed files plus the manifest and excludes only itself.',
        'files': records})
    base.write_text(directory / 'SHA256SUMS.txt', ''.join(f"{r['sha256']}  {r['path']}\n" for r in records)
                    + f'{base.sha(manifest)}  {manifest.name}\n')
    for record in records:
        if base.sha(directory / record['path']) != record['sha256']:
            raise ValueError('Staged payload hash mismatch')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intro-dir', required=True, type=Path)
    parser.add_argument('--technical-dir', required=True, type=Path)
    parser.add_argument('--document-checks', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--secret-scan', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT)
    args = parser.parse_args()
    revision = base.git_text('rev-parse', '--verify', args.revision + '^{commit}')
    output = args.output_dir.resolve()
    intro_dir, technical_dir = args.intro_dir.resolve(), args.technical_dir.resolve()
    review_path, scan_path = args.document_checks.resolve(), args.secret_scan.resolve()
    if revision != base.git_text('rev-parse', 'HEAD') or base.git_text('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Commit reviewed sources first and use the exact clean current HEAD')
    if output.exists() or not output.is_relative_to(ROOT / 'deliverables'):
        raise ValueError('Use a wholly new delivery directory inside this project')
    for path in [intro_dir, technical_dir, review_path]:
        if not path.is_relative_to(CASE) or not path.exists():
            raise ValueError('Only use verified v5 case inputs')
    if not scan_path.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a project-local history screening report')
    scan, review = read_json(scan_path), read_json(review_path)
    if scan.get('revision') != revision or not scan.get('passed') or scan.get('unresolved'):
        raise ValueError('Need a passing history screen for the exact frozen source')
    if base.git_text('rev-list', '--all', '--not', revision):
        raise ValueError('Some exported commit refs are outside the scanned HEAD history')
    base.auxiliary_ref_coverage(revision)
    if base.sha(base.VIDEO) != VIDEO_SHA:
        raise ValueError('The approved v5 video was changed')
    intro = base.unique_file(intro_dir, '.pdf', '作品介绍')
    technical = base.unique_file(technical_dir, '.pdf', '技术文档')
    if review.get('status') != 'passed' or not review.get('all_pages_visually_reviewed'):
        raise ValueError('Both new documents require complete rendered visual review')
    reviewed = {item['kind']: item for item in review['documents']}
    for kind, file in [('project_introduction', intro), ('technical', technical)]:
        if reviewed[kind]['sha256'] != base.sha(file) or not file.read_bytes().startswith(b'%PDF-'):
            raise ValueError('Reviewed PDF bytes differ from the chosen document')
    if intro.stat().st_size > 100_000_000 or reviewed['technical']['pages'] > 30:
        raise ValueError('Document size/page limit exceeded')
    facts_path = ROOT / 'docs/submission/20261031-v5/confirmed-facts.json'
    facts = read_json(facts_path)
    if not facts.get('confirmed_by_user') or review.get('confirmed_facts_sha256') != base.sha(facts_path):
        raise ValueError('Publication and project-origin facts must be confirmed and reviewed')
    old_checks = verify_old_files()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work = CASE / f'package-verification-{stamp}-{revision[:7]}'
    work.mkdir(parents=True, exist_ok=False)
    exported, canonical, bundle = work / 'git-archive-original.zip', work / 'frozen-source-canonical.zip', work / 'research-workspace-history.bundle'
    base.git('archive', '--format=zip', '--output=' + str(exported), revision)
    conversion = base.canonicalize_git_archive(exported, canonical, revision)
    base.git('bundle', 'create', str(bundle), '--all')
    integrity = base.verify_bundle_and_sources(canonical, bundle, revision, work)
    integrity['archive_conversion'] = conversion
    output.mkdir(parents=True, exist_ok=False)
    upload = output / '01_上传百度网盘_作品文件'
    single = output / '02_表单第6项_作品介绍文档'
    editable = output / '03_可编辑备份_无需上传'
    evidence = output / '04_校验与来源_源码包内亦有摘要'
    for directory in [upload, single, editable, evidence]:
        directory.mkdir(exist_ok=False)
    base.copy_new(intro, upload / '01_研序_作品介绍_郭剑宇.pdf')
    base.copy_new(intro, single / '研序_作品介绍_郭剑宇.pdf')
    base.copy_new(technical, upload / '02_研序_技术文档_郭剑宇_v4.pdf')
    base.copy_new(base.VIDEO, upload / '03_研序_真实页面演示_剪映正常配音_v5.mp4')
    for directory in [intro_dir, technical_dir]:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in {'.docx', '.md'}:
                base.copy_new(path, editable / path.name)
    image_source = ROOT / 'docs/submission/20261031-v5/images'
    for path in sorted(image_source.iterdir()):
        if path.is_file() and (path.suffix.lower() == '.png' or path.name == 'manifest.json'):
            base.copy_new(path, editable / 'images' / path.name)
    base.copy_new(technical_dir / 'architecture.png', editable / 'architecture.png')
    base.copy_new(base.VIDEO_ROOT / 'final-v5-r2/研序_真实页面演示_v5.srt', editable / '研序_真实页面演示_v5.srt')
    sources = []
    base.collect_evidence(evidence, sources)
    base.write_json(evidence / '最新源码与完整历史恢复.json', base.sanitize(integrity))
    base.write_json(evidence / '源码历史密钥模式检查.json', base.sanitize(scan))
    base.write_json(evidence / '文档完整检查.json', base.sanitize(review))
    base.write_json(evidence / '旧成品保护检查.json', old_checks)
    base.write_json(evidence / '确认事实.json', facts)
    base.write_json(evidence / '实际Prompt版本与源码清单.json', base.prompt_records(revision, integrity['source_files']))
    images = ROOT / 'docs/submission/20261031-v5/images'
    base.write_json(evidence / '真实页面配图来源.json', base.sanitize(read_json(images / 'manifest.json')))
    base.write_json(evidence / '历史证据来源映射.json', sources)
    base.copy_new(ROOT / 'docs/submission/20261031-v5/current-form-requirements.md', evidence / '当前表单材料对应.md')
    for directory, prefix in [(intro_dir, '作品介绍'), (technical_dir, '技术文档')]:
        info = directory / 'build-info.json'
        if info.is_file():
            base.write_json(evidence / (prefix + '构建阶段.json'), base.sanitize(read_json(info)))
    base.write_text(evidence / '验证范围说明.txt',
        '本轮验证作品文档、截图来源、源码、Git历史和文件完整性，不进行新的模型或全量软件验收。\n'
        '1277项为2026年10月27日历史本地回归；应用和测试179文件摘要另作核对。\n'
        '视频四场Flash演示共35次成功请求，来源与审核按各任务检查。翻译没有逐事实语义审核。\n'
        'Pro付费复测已取消，未验收范围继续保留。截图来自选定视频，不是新模型请求。\n'
        '恢复Git不包含异机依赖安装、模型准备、加载私库、公网部署或外部提交。\n'
        '证据路径脱敏；源码包含必要图片与实际Prompt，不包含真实密钥、私人数据库、私聊、模型权重或原始录屏。\n')
    history = work / 'history'
    history.mkdir(exist_ok=False)
    base.copy_new(bundle, history / bundle.name)
    base.write_text(history / 'git-log.txt', base.git_text('log', '--all', '--date=iso-strict', '--format=fuller', '--date-order') + '\n')
    base.write_text(history / 'bundle-heads.txt', integrity['bundle_heads'] + '\n')
    base.write_json(history / 'source-file-hashes.json', {'revision': revision, 'files': integrity['source_files']})
    base.write_json(history / 'restore-verification.json', base.sanitize(integrity))
    base.write_text(history / 'README_恢复与运行.txt',
        f'冻结源码：{revision}\n真实历史提交数：{integrity["head_commit_count"]}\n\n'
        '从本ZIP解压根目录执行，恢复目标必须尚不存在：\n'
        'git clone history/research-workspace-history.bundle restored-project\n'
        'git -C restored-project rev-parse HEAD\n'
        'git -C restored-project fsck --full\n\n'
        'project/来自同一冻结提交的原始Git blob，保留文件模式；换行导出转换已记录。\n'
        'bundle提供完整真实历史，git-log只作辅助。acceptance/包含必要的文档和历史证据。\n'
        '恢复后按README在专用虚拟环境安装并另备OCR/模型/公开论文；真实密钥自行在本机配置。\n'
        '本地bundle不能代替评审可访问的GitHub/Gitee地址，恢复校验不是异机或公网验收。\n')
    source_zip = upload / f'04_研序_源码与完整Git历史_{revision[:7]}.zip'
    with zipfile.ZipFile(source_zip, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        with zipfile.ZipFile(canonical) as original:
            for entry in original.infolist():
                if not entry.is_dir():
                    prefixed = copy(entry)
                    prefixed.filename = 'project/' + entry.filename
                    target.writestr(prefixed, original.read(entry))
        for prefix, directory in [('history', history), ('acceptance', evidence)]:
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    target.write(path, prefix + '/' + path.relative_to(directory).as_posix())
    with zipfile.ZipFile(source_zip) as target, zipfile.ZipFile(canonical) as original:
        if target.testzip() is not None or len(target.namelist()) != len(set(target.namelist())):
            raise ValueError('Final ZIP CRC or member uniqueness failed')
        for record in integrity['source_files']:
            if hashlib.sha256(target.read('project/' + record['path'])).hexdigest() != record['sha256']:
                raise ValueError('Final source ZIP blob mismatch')
            if target.getinfo('project/' + record['path']).external_attr != original.getinfo(record['path']).external_attr:
                raise ValueError('Final source ZIP file mode changed')
        if hashlib.sha256(target.read('history/' + bundle.name)).hexdigest() != base.sha(bundle):
            raise ValueError('Final Git bundle bytes changed')
    base.write_text(upload / '00_文件说明与提交指引.txt',
        '研序 可溯源科研助手 Agent\n参赛者：郭剑宇；个人参赛；指导老师：无。\n\n'
        '当前表单填写：\n第5项：上传本文件夹全部内容，填写有效网盘分享链接，建议长期有效并附提取码。\n'
        '第6项：单独上传01_研序_作品介绍_郭剑宇.pdf；此PDF涵盖要求的七项内容，不是旧版AI短说明。\n'
        '第7项：将本包v5视频另行上传B站、抖音等可公开访问的视频平台，填写公开视频链接；不得使用百度网盘链接。\n\n'
        '01为完整作品介绍；02为技术文档；03为选定v5视频；04为源码、完整Git历史及acceptance核对证据。\n'
        f'源码冻结：{revision}\n视频：5分34秒、1920×1080、剪映原生男声正常1倍速。\n'
        f'视频SHA-256：{VIDEO_SHA}\n\n'
        '阅读与运行：解压源码ZIP后，按project/README.md及history/README_恢复与运行.txt准备专用环境并启动。\n'
        '源码不含虚拟环境、私人数据库、原论文、OCR语言数据及模型权重；模型密钥由使用者在本机配置。\n'
        '作品未发表、未获奖、未基于其他项目改造的情况已由参赛者确认；第三方组件和AI协同另行说明。\n'
        '本轮无新增模型调用，Pro复测已取消；历史测试与演示均保留日期和适用范围，不代表全部功能验收完成。\n'
        '网盘分享、公开视频链接、评审仓库及公网演示按实际状态办理。本次只准备本地文件，不代为发布、部署或提交。\n')
    shared = {'source_revision': revision, 'source_head_commit_count': integrity['head_commit_count'],
              'source_all_ref_commit_count': integrity['all_ref_commit_count'],
              'source_archive_files': integrity['source_archive_files'], 'selected_video_version': 'v5 / final-v5-r2',
              'selected_video_sha256': VIDEO_SHA, 'current_form_fields': [5, 6, 7],
              'field6_document': '完整作品介绍PDF 七项内容', 'field7_requirement': 'Public video-platform URL; Baidu link is not allowed',
              'source_zip_crc_passed': True, 'source_blob_restore_passed': True,
              'bundle_verified': True, 'git_fsck_passed': True, 'old_artifacts_check': old_checks,
              'new_model_calls': 0, 'new_pro_calls': 0, 'new_full_regression_run': False,
              'uploaded': False, 'public_video_published': False, 'pushed': False, 'deployed': False, 'submitted': False}
    checksums(upload, '提交文件校验清单.json', {**shared, 'scope': 'Only the files to upload to Baidu'})
    base.write_text(output / '00_先读_按当前表单提交.txt',
        '新的本地提交材料 v5  2026年10月31日\n\n'
        '第5项：上传01_上传百度网盘_作品文件整个文件夹，生成分享链接和提取码后回填。\n'
        '第6项：只上传02_表单第6项_作品介绍文档内那一份PDF，与01中的作品介绍字节相同。\n'
        '第7项：将01中的v5视频另行发布到B站、抖音等可公开访问的视频平台，填写视频链接；不能使用百度网盘链接。\n'
        '03为可编辑备份，04为展开证据，不需要重复上传；必要证据也随源码ZIP提供。\n'
        '旧表单要求的AI创作短说明和第6项旧指引不适用于当前完整作品介绍要求，请以本目录为准。\n\n'
        f'源码冻结：{revision}\n本包只整理本地文件，旧材料全部保留，没有覆盖旧成品。\n'
        '尚需外部办理：有效网盘分享、公开视频平台链接、评审仓库及公网演示，以及实际报名字段。\n')
    if verify_old_files() != old_checks:
        raise ValueError('Old artifact protection changed during packaging')
    payload = checksums(output, '材料校验清单.json', {**shared, 'scope': 'Complete local delivery including editable backups and evidence',
                       'source_zip': source_zip.relative_to(output).as_posix(),
                       'created_utc': datetime.now(timezone.utc).isoformat(),
                       'pending_external_items': ['Baidu share URL/extraction code', 'Public video-platform URL', 'Evaluator repository URL', 'Public demo URL/accounts', 'Registration fields']})
    base.write_json(work / 'package-result.json', {'revision': revision, 'output': output.relative_to(ROOT).as_posix(),
                    'payload_files': len(payload), 'source_zip_sha256': base.sha(source_zip),
                    'restore_passed': True, 'crc_passed': True, 'old_artifact_files_unchanged': old_checks['checked_files']})
    print(json.dumps({'output': str(output), 'revision': revision, 'files': len(payload),
                      'source_files': integrity['source_archive_files'], 'commits': integrity['head_commit_count'],
                      'source_zip_bytes': source_zip.stat().st_size, 'old_files_unchanged': old_checks['checked_files'],
                      'new_model_calls': 0, 'uploaded': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
