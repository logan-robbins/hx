"""Bounded, evaluated MSBuild project/dependency identities.

XML is used to read MSBuild's preprocessed import boundaries, never to infer
conditional project references or compile item lists from project source XML.
Identities are relocated through `anchors` (default: the checkout and the support
store's root), so equal inputs in two worktrees or two roots hash alike.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

from . import checks, fingerprints
from .continuity_store import digest
from .errors import ValidationError

PROPERTIES = ('MSBuildAllProjects', 'MSBuildToolsPath', 'NETCoreSdkVersion', 'TargetFramework',
    'TargetFrameworks', 'RuntimeIdentifier', 'Configuration', 'TargetPath', 'ProjectAssetsFile',
    'ArtifactsPath', 'BaseIntermediateOutputPath', 'BaseOutputPath', 'MSBuildProjectName',
    'MSBuildSDKsPath', 'NetCoreTargetingPackRoot', 'IsTestProject', 'IsTestingPlatformApplication',
    'TestingPlatformDotnetTestSupport', 'UsingMicrosoftNETSdk', 'TargetFrameworkIdentifier',
    'RestoreConfigFile', 'RestoreLockedMode', 'RestorePackagesWithLockFile', 'RestorePackagesPath',
    'DefineConstants', 'LangVersion', 'Nullable', 'Optimize', 'DebugSymbols', 'DebugType',
    'Deterministic', 'PathMap', 'Platform', 'PlatformTarget', 'OutputType', 'AssemblyName',
    'RootNamespace', 'AssemblyVersion', 'FileVersion', 'InformationalVersion', 'Version',
    'TreatWarningsAsErrors', 'WarningsAsErrors', 'NoWarn', 'AnalysisLevel', 'EnableNETAnalyzers',
    'EnableDefaultItems', 'EnableDefaultCompileItems', 'GenerateAssemblyInfo', 'GenerateTargetFrameworkAttribute',
    'UseAppHost', 'SelfContained', 'PublishAot', 'PublishTrimmed', 'CompilerResponseFile')
ITEMS = ('Compile', 'ProjectReference', 'Analyzer', 'PackageReference', 'AdditionalFiles',
    'EditorConfigFiles', 'None', 'Content', 'EmbeddedResource', 'Reference', 'FrameworkReference')
MAX_QUERY_BYTES = 16 * 1024 * 1024
MAX_PROJECTS = 128


def invoke(argv, cwd, env, *, timeout_s=60, max_bytes=MAX_QUERY_BYTES):
    with tempfile.TemporaryDirectory(prefix='hx-dotnet-eval-') as directory:
        output = Path(directory) / 'output'
        result = checks.execute(argv, cwd, env, output, timeout_s, max_bytes)
        return result, output.read_text(errors='replace')


def properties(configuration='Debug', framework=None, runtime=None, artifacts_path=None, packages_path=None, platform=None):
    if not isinstance(configuration, str) or not configuration or any(c in configuration for c in '\0;,'):
        raise ValidationError('invalid MSBuild Configuration')
    result = [f'-p:Configuration={configuration}', '-p:UseSharedCompilation=false']
    for name, value in (('TargetFramework', framework), ('RuntimeIdentifier', runtime), ('ArtifactsPath', artifacts_path), ('RestorePackagesPath', packages_path), ('Platform', platform)):
        if value is not None:
            if not isinstance(value, str) or not value or any(c in value for c in '\0;,'):
                raise ValidationError(f'invalid MSBuild {name}')
            result.append(f'-p:{name}={value}')
    if artifacts_path is not None:
        result.append('-p:UseArtifactsOutput=true')
    return result


def _global_arguments(values):
    """Pass SDK-evaluated globals as literal MSBuild values, including solution XML."""
    def escaped(value):
        for source, replacement in (('%','%25'), (';','%3B'), (',','%2C'), ('$','%24'), ('@','%40'), ("'",'%27'), ('(','%28'), (')','%29')):
            value = value.replace(source, replacement)
        return value
    return ['-p:' + name + '=' + escaped(value) for name, value in sorted(values.items())]


def _json(text):
    # SDK first-use informational text can precede structured output. Never use
    # the first brace of an error as a successful graph evaluation.
    at = text.find('{')
    if at < 0:
        raise ValueError('MSBuild did not emit JSON')
    value = json.loads(text[at:])
    if not isinstance(value, dict) or not isinstance(value.get('Properties'), dict) or not isinstance(value.get('Items'), dict):
        raise ValueError('MSBuild emitted an unsupported evaluation schema')
    return value


def _path(value, project):
    path = Path(value)
    return path if path.is_absolute() else project.parent / path


def _imports(preprocessed, project):
    root = ET.fromstring(preprocessed)
    if root.tag.rsplit('}', 1)[-1] != 'Project':
        raise ValueError('preprocess did not emit a project')
    paths = {project}
    for comment in re.findall(r'<!--(.*?)-->', preprocessed, re.S):
        if '====' not in comment:
            continue
        for line in comment.splitlines():
            path = Path(line.strip())
            if path.is_absolute() and path.suffix.lower() in ('.props', '.targets', '.proj', '.csproj', '.fsproj', '.vbproj'):
                paths.add(path)
    return paths


def _global_files(cwd):
    paths = set()
    for folder in (cwd, *cwd.parents):
        for name in ('global.json', 'NuGet.Config', 'nuget.config', 'Directory.Packages.props',
                     'Directory.Build.props', 'Directory.Build.targets', 'Directory.Build.rsp'):
            candidate = folder / name
            if candidate.is_file():
                paths.add(candidate)
    return paths


def evaluate(project, cwd, env, *, dotnet='dotnet', configuration='Debug', framework=None,
             runtime=None, platform=None, artifacts_path=None, source_contract=None, sdk=None, support_store=None,
             anchors=None):
    cwd = Path(cwd).resolve()
    if anchors is None:
        anchors = fingerprints.anchors_for(cwd, *([support_store.root] if support_store is not None else []))

    def relocate(value):
        return fingerprints.relocate(value, anchors)
    project = (cwd / project).resolve()
    solution = project.suffix.lower() in ('.sln', '.slnx')
    if not project.is_file() or project.suffix.lower() not in ('.csproj', '.fsproj', '.vbproj', '.sln', '.slnx'):
        raise ValidationError('build requires an existing project or .sln/.slnx entry point')
    args = properties(configuration, framework, runtime, str(artifacts_path) if artifacts_path else None, env.get('NUGET_PACKAGES'), platform)
    if sdk is None:
        observed, info = invoke([dotnet, '--info'], cwd, env)
        help_result, help_text = invoke([dotnet, 'msbuild', '-help'], cwd, env)
        sdk = {'info_hash': digest(relocate(info)), 'query': '-getProperty' in help_text and '-getItem' in help_text,
               'graph_build': '-graphBuild' in help_text, 'version': '',
               'available': observed['exit_code'] == 0 and help_result['exit_code'] == 0}
    reasons = set()
    if not sdk['available'] or not sdk['query']:
        return {'project': str(project), 'configuration': configuration,
                'framework': framework, 'runtime': runtime, 'nodes': [], 'sdk': sdk,
                'complete': False, 'reasons': ['SDK lacks evaluated property/item query support'],
                'hash': digest(relocate({'project': str(project), 'args': args, 'sdk': sdk})), 'anchors': anchors}
    if not sdk['graph_build']:
        reasons.add('SDK lacks static graph build support')
    inputs = {project, *_global_files(cwd)}
    if any(path.name == 'Directory.Build.rsp' for path in inputs):
        reasons.add('automatic MSBuild response files are disabled; their options are not modeled')
    managed_inputs = set()
    env_inputs = set()
    nodes = []
    if solution:
        solution_custom = set()
        for folder in (project.parent,*project.parent.parents):
            for name in ('Directory.Solution.props','Directory.Solution.targets'):
                candidate=folder/name
                if candidate.is_file(): solution_custom.add(candidate)
        for name in ('before.'+project.name+'.targets','after.'+project.name+'.targets'):
            candidate=project.parent/name
            if candidate.is_file(): solution_custom.add(candidate)
        if solution_custom:
            inputs.update(solution_custom)
            reasons.add('custom solution targets require an evaluated solution target/import contract')
        from . import dotnet_solution
        mapping = dotnet_solution.mapping(project, cwd, env, dotnet=dotnet, configuration=configuration,
            framework=framework, runtime=runtime, platform=platform, artifacts_path=artifacts_path, store=support_store)
        if mapping['complete']:
            todo = [(Path(node['project']), node['global_properties'].get('TargetFramework'), node['global_properties'], node['references']) for node in mapping['nodes']]
        else:
            listing, text = invoke([dotnet, 'sln', str(project), 'list'], cwd, env)
            roots = [line.strip() for line in text.splitlines() if Path(line.strip()).suffix.lower() in ('.csproj', '.fsproj', '.vbproj')]
            if listing['exit_code'] or listing['reason'] or not roots:
                reasons.add('SDK solution project enumeration failed')
            reasons.add('SDK solution configuration mapping is unavailable: ' + mapping['reason'])
            todo = [((_path(name, project)).resolve(), framework, None, None) for name in roots]
    else:
        mapping = None
        todo = [(project, framework, None, None)]
    visited = set()
    project_names = {}
    output_root = Path(artifacts_path).resolve() if artifacts_path else None
    while todo:
        path, tfm, globals, reference_contexts = todo.pop(0)
        key = (str(path), tfm, digest(globals))
        if key in visited:
            continue
        visited.add(key)
        if len(visited) > MAX_PROJECTS:
            reasons.add('evaluated graph exceeds 128 project/framework nodes')
            break
        node_args = _global_arguments(globals) if globals is not None else properties(configuration, tfm, runtime, str(artifacts_path) if artifacts_path else None, env.get('NUGET_PACKAGES'), platform)
        command = [dotnet, 'msbuild', str(path), '-nologo', '-noAutoResponse', '-nodeReuse:false', *node_args]
        observation, raw = invoke([*command, '-getProperty:' + ','.join(PROPERTIES), '-getItem:' + ','.join(ITEMS)], cwd, env)
        if observation['exit_code'] != 0 or observation['reason']:
            reasons.add('MSBuild evaluated query failed')
            continue
        try:
            value = _json(raw)
            props, items = value['Properties'], value['Items']
            for name in PROPERTIES:
                if name not in props or not isinstance(props[name], str):
                    raise ValueError('missing queried property')
            for name in ITEMS:
                if not isinstance(items.get(name), list) or len(items[name]) > 100000:
                    raise ValueError('missing or excessive evaluated item list')
            pp_result, preprocessed = invoke([*command, '-preprocess'], cwd, env)
            if pp_result['exit_code'] != 0 or pp_result['reason']:
                raise ValueError('preprocess failed')
            imports = _imports(preprocessed, path)
        except (ValueError, ET.ParseError):
            reasons.add('evaluated imports/items are incomplete or unsupported')
            continue
        inputs.update(imports)
        for imported in props['MSBuildAllProjects'].split(';'):
            if imported:
                inputs.add(_path(imported, path))
        sdk_root = Path(props['MSBuildToolsPath'])
        if not sdk_root.is_absolute() or not sdk_root.is_dir() or not props['NETCoreSdkVersion']:
            reasons.add('SDK toolchain path/version is unavailable')
        else:
            env_inputs.add(sdk_root)
            sdk = {**sdk, 'version': props['NETCoreSdkVersion']}
            # Roslyn/MSBuild execute through the selected hostfxr and shared CLR,
            # whose binaries can change independently from the SDK directory.
            installation = sdk_root.parent.parent if sdk_root.parent.name == 'sdk' else None
            roots = [installation, Path(env['DOTNET_ROOT']) if env.get('DOTNET_ROOT') else None]
            for deployment in roots:
                if deployment:
                    for folder in ('host', 'shared'):
                        candidate = deployment / folder
                        if candidate.is_dir():
                            env_inputs.add(candidate)
        packs = props['NetCoreTargetingPackRoot']
        if packs:
            env_inputs.add(Path(packs))
        if props['UsingMicrosoftNETSdk'].lower() != 'true':
            reasons.add('project does not use SDK artifacts output isolation')
        frameworks = [v for v in props['TargetFrameworks'].split(';') if v]
        if globals is None and not tfm and frameworks:
            todo.extend((path, target, None, None) for target in frameworks)
        refs = [ref['project'] for ref in reference_contexts] if reference_contexts is not None else []
        item_paths = []
        packages = items['PackageReference']
        if props['CompilerResponseFile']:
            for response in props['CompilerResponseFile'].split(';'):
                if response:
                    inputs.add(_path(response, path))
            if not (source_contract and source_contract.get('complete') is True):
                reasons.add('compiler response files require a complete declared dependency closure')
        if any('*' in str(item.get('Version', '')) or str(item.get('Version', '')).startswith(('[', '(')) for item in packages) and props['RestoreLockedMode'].lower() != 'true':
            reasons.add('floating/ranged NuGet dependencies require a locked restore manifest')
        if props['RestoreConfigFile']:
            inputs.add(_path(props['RestoreConfigFile'], path))
        if env.get('NUGET_PACKAGES') and props['RestorePackagesPath'] != env['NUGET_PACKAGES']:
            reasons.add('project overrides isolated SDK package cache')
        for item_type, entries in items.items():
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get('Identity'), str):
                    reasons.add('evaluated item metadata is malformed')
                    continue
                if item_type in ('PackageReference', 'FrameworkReference'):
                    continue
                name = item.get('FullPath') or item['Identity']
                candidate = _path(name, path).resolve()
                if item_type == 'ProjectReference':
                    if globals is None and (item.get('GlobalPropertiesToRemove') or item.get('SetConfiguration') or item.get('SetPlatform') or item.get('AdditionalProperties')):
                        reasons.add('project reference has unmodeled global property overrides')
                    ref_tfm = item.get('SetTargetFramework', '')
                    if ref_tfm.startswith('TargetFramework='):
                        ref_tfm = ref_tfm.split('=', 1)[1]
                    elif ref_tfm:
                        reasons.add('project reference has unmodeled framework override')
                        ref_tfm = None
                    else:
                        ref_tfm = None
                    if globals is None:
                        refs.append(str(candidate))
                        todo.append((candidate, ref_tfm, None, None))
                if item_type == 'Reference' and not item.get('HintPath'):
                    reasons.add('assembly reference has no resolved path')
                    continue
                if item_type == 'Reference':
                    candidate = _path(item['HintPath'], path).resolve()
                # Generated obj/bin inputs belong to the immutable build artifact,
                # not its source readset. Their derivation is evaluated separately.
                if output_root and candidate.is_relative_to(output_root):
                    managed_inputs.add(candidate)
                elif candidate.is_file():
                    inputs.add(candidate)
                    item_paths.append({'kind': item_type, 'path': str(candidate)})
                elif item_type not in ('None', 'Content'):
                    reasons.add('evaluated required input is absent')
        assets_name = props['ProjectAssetsFile']
        assets = _path(assets_name, path) if assets_name else None
        package_paths = []
        if assets and assets.is_file():
            managed_inputs.add(assets)
            try:
                if assets.stat().st_size > MAX_QUERY_BYTES:
                    raise ValueError('assets exceed bound')
                manifest = json.loads(assets.read_text())
                for config in manifest.get('project', {}).get('restore', {}).get('configFilePaths', []):
                    inputs.add(Path(config))
                folders = [Path(folder) for folder in manifest.get('packageFolders', {})]
                for library in manifest.get('libraries', {}).values():
                    if library.get('type') != 'package':
                        continue
                    relative = library.get('path')
                    found = next((folder / relative for folder in folders if relative and (folder / relative).is_dir()), None)
                    if found is None:
                        reasons.add('resolved NuGet package content is unavailable')
                    else:
                        env_inputs.add(found)
                        package_paths.append(str(found))
                manifest_identity = digest(relocate(manifest))
            except (OSError, ValueError, TypeError):
                reasons.add('resolved NuGet assets manifest is invalid')
                manifest_identity = None
        else:
            manifest_identity = None
            if packages:
                reasons.add('package references lack a resolved assets manifest')
        # Arbitrary custom targets can read files not exposed by item evaluation.
        # Only an explicit complete source contract can certify those side inputs.
        if not (source_contract and source_contract.get('complete') is True):
            for imported in imports:
                if imported.is_relative_to(sdk_root) or (packs and imported.is_relative_to(Path(packs))):
                    continue
                try:
                    custom = ET.parse(imported).getroot()
                    if any(node.tag.rsplit('}', 1)[-1] in ('Target', 'UsingTask') for node in custom.iter()):
                        reasons.add('custom targets/generators require a complete declared source closure')
                except (OSError, ET.ParseError):
                    reasons.add('custom imported project is unavailable')
        name = props['MSBuildProjectName']
        if name in project_names and project_names[name] != str(path):
            reasons.add('duplicate project names collide in SDK artifacts layout')
        project_names[name] = str(path)
        if output_root:
            for field in ('BaseIntermediateOutputPath', 'BaseOutputPath'):
                if not props[field] or not _path(props[field], path).resolve().is_relative_to(output_root):
                    reasons.add('project overrides isolated SDK output directories')
            if props['TargetPath'] and not _path(props['TargetPath'], path).resolve().is_relative_to(output_root):
                reasons.add('project overrides isolated SDK target output')
        nodes.append({'project': str(path), 'framework': props['TargetFramework'], 'properties': props,
                      'references': sorted(refs), 'inputs': sorted(item_paths, key=lambda x: (x['kind'], x['path'])),
                      'imports': sorted(str(p) for p in imports), 'assets_hash': manifest_identity,
                      'package_paths': sorted(package_paths), 'global_properties': globals,
                      'reference_contexts': reference_contexts})
    # Inputs inside the SDK/package closure are fingerprinted as execution state.
    source_paths = [str(p) for p in sorted(inputs) if not output_root or not p.resolve().is_relative_to(output_root)]
    source = fingerprints.source(cwd, source_paths, env, contract=source_contract, anchors=anchors)
    environment, resolved = fingerprints.environment(cwd, [dotnet],
        {'complete': True, 'executables': [], 'inputs': [str(p) for p in sorted(env_inputs)], 'external_versions': {}}, env,
        anchors=anchors)
    from . import dotnet_git
    repository_workdirs = sorted({str(Path(node['project']).parent) for node in nodes})
    environment = dotnet_git.bind(environment, repository_workdirs, source['declared_inputs'], env, anchors)
    if not nodes:
        reasons.add('no evaluated project nodes')
    if not source['complete']:
        reasons.update(source['reasons'])
    if not environment['complete']:
        reasons.update(environment['reasons'])
    identity_nodes = []
    for node in nodes:
        # File timestamps in item metadata are observations, not semantic keys.
        # Only actual evaluated values that influence compilation enter this key,
        # relocated and then ordered so every checkout lists them alike.
        identity = relocate({key: node[key] for key in ('project', 'framework', 'references', 'inputs', 'imports', 'assets_hash',
                                                         'package_paths', 'properties', 'global_properties', 'reference_contexts')})
        for key in ('references', 'imports', 'package_paths'):
            identity[key] = sorted(identity[key])
        identity['inputs'] = sorted(identity['inputs'], key=lambda x: (x['kind'], x['path']))
        identity_nodes.append(identity)
    result = {'project': str(project), 'cwd': str(cwd), 'configuration': configuration,
              'framework': framework, 'runtime': runtime, 'platform': platform, 'sdk': sdk, 'nodes': nodes,
              'solution_mapping': mapping,
              'source': source, 'environment': environment, 'resolved_dotnet': resolved[0],
              'dotnet': dotnet, 'environment_inputs': [str(p) for p in sorted(env_inputs)],
              'repository_workdirs': repository_workdirs,
              'complete': not reasons, 'reasons': sorted(reasons), 'managed_inputs': sorted(str(p) for p in managed_inputs),
              'anchors': anchors}
    result['hash'] = digest(relocate({'recipe': [str(project), configuration, framework, runtime, platform, str(output_root)],
                                      'nodes': identity_nodes, 'source': source['hash'], 'environment': environment['hash'], 'sdk': sdk}))
    return result


def observe(graph, env):
    """Recheck the captured readset at an artifact publication boundary."""
    source = graph['source']
    anchors = graph['anchors']
    contract = {'complete': True, 'inputs': []} if source['closure_complete'] else None
    current = fingerprints.source(Path(graph['cwd']), source['declared_inputs'], env, contract=contract, anchors=anchors)
    environment, _ = fingerprints.environment(Path(graph['cwd']), [graph['dotnet']],
        {'complete': True, 'executables': [], 'inputs': graph['environment_inputs'], 'external_versions': {}}, env,
        anchors=anchors)
    from . import dotnet_git
    environment = dotnet_git.bind(environment, graph.get('repository_workdirs', []), source['declared_inputs'], env, anchors)
    return current, environment
