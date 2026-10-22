"""Bounded Git metadata that can affect SDK SourceLink and version output.

Configuration values never enter persisted metadata. Tracked-state identity is
restricted to the evaluated source readset; raw index observations still detect
write-and-restore operations during execution. Identity is portable: the checkout
enters as `{repository}` and configuration by its relocated values, not the files
that hold them, so linked worktrees of one clone at one commit share it.
"""
from __future__ import annotations

from pathlib import Path

from . import dotnet_graph, fingerprints
from .continuity_store import digest


def _run(cwd, env, *args, max_bytes=dotnet_graph.MAX_QUERY_BYTES):
    return dotnet_graph.invoke(['git', '--no-optional-locks', '-C', str(cwd), *args], cwd, env, max_bytes=max_bytes)


def _observations(paths):
    values = []
    complete = True
    for path in sorted(paths):
        try:
            info = path.lstat()
            target = path.stat()
            values.append([str(path), fingerprints._stamp(info), fingerprints._stamp(target)])
        except FileNotFoundError:
            values.append([str(path), 'absent'])
        except OSError:
            complete = False
            values.append([str(path), 'unavailable'])
    return digest(values), complete


def identity(cwd, inputs, env, anchors=None):
    cwd = Path(cwd)
    anchors = fingerprints.anchors_for(cwd) if anchors is None else anchors
    observed, text = _run(cwd, env, 'rev-parse', '--absolute-git-dir', max_bytes=8192)
    markers = any((folder / '.git').exists() for folder in (cwd, *cwd.parents))
    if observed['exit_code'] or observed['reason']:
        return {'hash': digest(None), 'observation_hash': digest(None),
                'complete': not markers, 'repository': None}
    gitdir = Path(text.strip()).resolve()
    common_result, common = _run(cwd, env, 'rev-parse', '--git-common-dir', max_bytes=8192)
    root_result, root = _run(cwd, env, 'rev-parse', '--show-toplevel', max_bytes=8192)
    common = Path(common.strip())
    common = common.resolve() if common.is_absolute() else (cwd / common).resolve()
    repository = Path(root.strip()).resolve()
    metadata = {folder / name for folder in (gitdir, common) for name in
                ('HEAD', 'config', 'config.worktree', 'index', 'packed-refs', 'shallow')}
    symbolic_result, symbolic = _run(cwd, env, 'symbolic-ref', '-q', 'HEAD', max_bytes=8192)
    if not symbolic_result['exit_code'] and symbolic.strip():
        metadata.add(common / symbolic.strip())
    head_result, head = _run(cwd, env, 'rev-parse', '--verify', 'HEAD', max_bytes=8192)
    config_result, configuration = _run(cwd, env, 'config', '--null', '--list', '--show-origin')
    # --null --show-origin emits alternating origin and key/value fields.
    fields = configuration.split('\0')
    for origin in fields[::2]:
        if origin.startswith('file:'):
            path = Path(origin[5:])
            # Git config resolves relative origin filenames from the repository
            # root after discovery, even when -C names a nested project folder.
            metadata.add(path if path.is_absolute() else repository / path)
    tracked_result, tracked = _run(cwd, env, 'ls-files', '--cached', '-z')
    tracked_paths = set(tracked.split('\0'))
    relevant = set()
    for name in inputs:
        candidate = Path(name)
        candidate = candidate if candidate.is_absolute() else cwd / candidate
        try:
            relative = candidate.resolve().relative_to(repository).as_posix()
        except ValueError:
            continue
        if relative in tracked_paths:
            relevant.add(relative)
    observation_hash, available = _observations(metadata)
    complete = available and gitdir.is_dir() and repository.is_dir() and all(not value['exit_code'] and not value['reason'] for value in
                                (common_result, root_result, config_result, tracked_result))
    # An unborn HEAD is a known repository state. Invalid or truncated command
    # output still prevents reuse through the bounded process observations.
    if head_result['reason'] or head_result['exit_code'] not in (0, 128):
        complete = False
    return {'hash': digest({'repository': fingerprints.relocate(str(repository), anchors),
                            'head': head.strip() if head_result['exit_code'] == 0 else None,
                            'configuration': digest(fingerprints.relocate(fields[1::2], anchors)),
                            'tracked_inputs': sorted(relevant)}),
            'observation_hash': observation_hash, 'complete': complete, 'repository': str(repository)}


def bind(environment, workdirs, inputs, env, anchors=None):
    values = []
    repositories = set()
    # With Git overrides, cwd can affect resolution; evaluate each context.
    # Ordinary project nodes sharing one Git directory share its metadata pass.
    overrides = any(key.startswith('GIT_') for key in env)
    for folder in sorted(set(workdirs)):
        discovered, location = _run(Path(folder), env, 'rev-parse', '--absolute-git-dir', max_bytes=8192)
        key = location.strip() if not discovered['exit_code'] and not discovered['reason'] else str(folder)
        if not overrides and key in repositories:
            continue
        repositories.add(key)
        values.append(identity(folder, inputs, env, anchors))
    result = dict(environment)
    result['hash'] = digest({'environment': environment['hash'], 'git': [value['hash'] for value in values]})
    result['observation_hash'] = digest({'environment': environment['observation_hash'],
                                        'git': [value['observation_hash'] for value in values]})
    result['git_complete'] = all(value['complete'] for value in values)
    result['complete'] = environment['complete'] and result['git_complete']
    if not result['git_complete']:
        result['reasons'] = sorted({*environment['reasons'], 'Git metadata identity is unavailable'})
    return result
