"""Build a portable source/build delivery with per-file hashes, never local data.

Uses the current working tree, including newly created source files. Requires
only Python's standard library and refuses to overwrite an existing archive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile


CODE = Path(__file__).resolve().parents[2]
REPO = CODE.parent
EXCLUDED_DIRS = {'.git', '.venv', 'venv', 'node_modules', 'output', '__pycache__',
                 '.pytest_cache', '.ruff_cache', 'test-results', 'playwright-report',
                 'backups', 'backup', 'db', 'data'}
SOURCE_EXTENSIONS = {'.py', '.ps1', '.md', '.txt', '.json', '.js', '.jsx', '.css',
                     '.html', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico',
                     '.woff', '.woff2', '.ttf', '.xml', '.pdf', '.docx', '.toml',
                     '.yaml', '.yml', '.ini'}
ARCHIVED_EVIDENCE_EXTENSIONS = {'.tap', '.zip', '.webmanifest', '.log'}
EVIDENCE_NAME = re.compile(r'^(?:run-summary\.json|results\.json|(?:backend|frontend)-junit\.xml|source-manifest-(?:before|after)\.json|frontend-source-dist-sha256\.json|npm-test-tool-transcript\.txt|cross-statistics-[\w-]+\.png|[\w.-]+\.tap)$')


def is_link(path):
    return path.is_symlink() or path.is_junction()


def safe_source(path):
    if is_link(path) or not path.resolve(strict=True).is_relative_to(REPO.resolve()):
        raise ValueError('Source must be an ordinary file inside the repository: ' + str(path))
    return path


def excluded_path(parts):
    """Keep browser report attachments while excluding runtime databases."""
    for index, part in enumerate(parts):
        if part.casefold() not in EXCLUDED_DIRS:
            continue
        if (part == 'data' and index > 0 and parts[index - 1] == 'report'
                and '03_测试' in parts[:index] and '证据' in parts[:index]):
            continue
        return True
    return False


def source_files():
    for name in ('README.md', 'update.md', '.gitignore'):
        path = REPO / name
        if name == 'update.md' and not path.exists():
            continue
        yield safe_source(path)
    for base in (CODE, REPO / '02_文档', REPO / '03_测试'):
        for directory, names, files in os.walk(base, followlinks=False):
            names[:] = sorted(name for name in names if not excluded_path((Path(directory) / name).relative_to(REPO).parts)
                              and not is_link(Path(directory) / name))
            for name in sorted(files):
                path = Path(directory) / name
                if is_link(path):
                    continue
                if name == '.env.example':
                    yield safe_source(path)
                elif not name.casefold().startswith('.env') and (
                    path.suffix.lower() in SOURCE_EXTENSIONS
                    or (path.is_relative_to(REPO / '03_测试' / '证据')
                        and path.suffix.lower() in ARCHIVED_EVIDENCE_EXTENSIONS)
                ):
                    yield safe_source(path)


def verify_archive(output, root_name, entries):
    # Explicit checks remain active under python -O / PYTHONOPTIMIZE.
    expected = {root_name + '/' + item['path'] for item in entries}
    expected.update({root_name + '/交付清单.json', root_name + '/交付须知.md'})
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError('Archive members differ from the delivery manifest')
        if archive.testzip() is not None:
            raise ValueError('Archive CRC verification failed')
        stored_manifest = json.loads(archive.read(root_name + '/交付清单.json'))
        if stored_manifest['files'] != entries or stored_manifest['fileCount'] != len(entries):
            raise ValueError('Stored delivery manifest differs from the source list')
        for entry in entries:
            raw = archive.read(root_name + '/' + entry['path'])
            if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Archive SHA-256 verification failed: ' + entry['path'])
        if any(excluded_path(Path(name).parts) for name in names):
            raise ValueError('Archive contains an excluded runtime directory')
        if any(Path(name).name.casefold().startswith('.env') and Path(name).name != '.env.example' for name in names):
            raise ValueError('Archive contains local environment configuration')


def build(output, version, evidence):
    output = Path(output).resolve()
    if output.exists() or output.with_suffix(output.suffix + '.sha256').exists():
        raise FileExistsError('Delivery archive and SHA-256 sidecar must both be new')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = {path.relative_to(REPO).as_posix(): path for path in source_files()}
    for supplied in evidence:
        path = Path(supplied).resolve()
        if not path.is_file() or not EVIDENCE_NAME.fullmatch(path.name):
            raise ValueError('Evidence must be an explicitly supported test record or QA screenshot')
        safe_source(path)
        name = '验收记录/' + path.parent.name + '/' + path.name
        if name in files:
            raise ValueError('Duplicate evidence destination: ' + name)
        files[name] = path
    root_name = '问序_GitHub'
    entries = []
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            archive.writestr(root_name + '/' + name, raw)
            entries.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        manifest = dict(version=version, createdAtUtc=datetime.now(timezone.utc).isoformat(),
                        scope='Current source, documentation, tests, production frontend and selected acceptance records. Local credentials, dependencies and runtime databases excluded.',
                        files=entries, fileCount=len(entries))
        archive.writestr(root_name + '/交付清单.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        archive.writestr(root_name + '/交付须知.md',
            f'# 问序 {version} 交付包\n\n'
            '安装与启动见 [项目 README](README.md)，测试范围与结果见 [测试说明](03_测试/README.md)，旧问卷迁移见 [迁移说明](02_文档/12_旧问卷显式迁移说明.md)。\n\n'
            '源文件、构建产物及选入的验收记录的 SHA-256 见 `交付清单.json`；清单不包含自身和本须知。\n\n'
            '包内不含本机 .env、签名密钥、虚拟环境、node_modules 或数据库文件。首次使用运行配置脚本生成本机密钥；不预置账号或真实答卷。\n')
    verify_archive(output, root_name, entries)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with output.with_suffix(output.suffix + '.sha256').open('x', encoding='utf-8') as stream:
        stream.write(digest + '  ' + output.name + '\n')
    return dict(path=str(output), sha256=digest, files=len(entries) + 2, bytes=output.stat().st_size, verified=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New archive path; existing archives are never overwritten')
    parser.add_argument('--version', default='2.2.0')
    parser.add_argument('--evidence', action='append', default=[], type=Path)
    args = parser.parse_args(argv)
    result = build(args.output, args.version, args.evidence)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
