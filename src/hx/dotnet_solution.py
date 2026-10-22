"""Authoritative solution project contexts from the installed MSBuild API.

The reader has no package dependencies. Its one-time offline bootstrap uses the
same durable broker, host slots and immutable artifacts as product builds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from xml.sax.saxutils import escape

from . import dotnet_graph, fingerprints
from .continuity_store import Conflict, digest
from .errors import ValidationError

SOURCE = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.Loader;
using System.Text.Json;
using Microsoft.Build.Construction;
using Microsoft.Build.Evaluation;
using Microsoft.Build.Graph;

class Reader {
    static int Main(string[] args) {
        string sdk = args[0];
        AssemblyLoadContext.Default.Resolving += (context, name) => {
            string path = Path.Combine(sdk, name.Name + ".dll");
            return File.Exists(path) ? context.LoadFromAssemblyPath(path) : null;
        };
        Environment.SetEnvironmentVariable("MSBUILD_EXE_PATH", Path.Combine(sdk, "MSBuild.dll"));
        if (Environment.GetEnvironmentVariable("MSBuildSDKsPath") == null)
            Environment.SetEnvironmentVariable("MSBuildSDKsPath", Path.Combine(sdk, "Sdks"));
        if (Environment.GetEnvironmentVariable("MSBuildExtensionsPath") == null)
            Environment.SetEnvironmentVariable("MSBuildExtensionsPath", sdk);
        return Evaluate(args);
    }
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Evaluate(string[] args) {
        try {
            var properties = JsonSerializer.Deserialize<Dictionary<string,string>>(File.ReadAllText(args[2]));
            var solution = SolutionFile.Parse(args[1]);
            if (!properties.ContainsKey("Platform")) properties["Platform"] = solution.GetDefaultPlatformName();
            using var collection = new ProjectCollection();
            var graph = new ProjectGraph(args[1], properties, collection);
            var nodes = graph.ProjectNodes.Select(node => new {
                project = node.ProjectInstance.FullPath,
                global_properties = node.ProjectInstance.GlobalProperties.Keys.ToDictionary(
                    key => key, key => node.ProjectInstance.GetPropertyValue(key)),
                references = node.ProjectReferences.Select(reference => new {
                    project = reference.ProjectInstance.FullPath,
                    global_properties = reference.ProjectInstance.GlobalProperties.Keys.ToDictionary(
                        key => key, key => reference.ProjectInstance.GetPropertyValue(key))
                }).ToArray()
            }).ToArray();
            Console.WriteLine(JsonSerializer.Serialize(new {
                complete = true, platform = properties["Platform"], nodes = nodes
            }));
            return 0;
        } catch (Exception error) {
            Console.WriteLine(JsonSerializer.Serialize(new {
                complete = false, reason = error.GetType().Name
            }));
            return 1;
        }
    }
}
'''


def _sdk(cwd, env, dotnet):
    version_result, version = dotnet_graph.invoke([dotnet, '--version'], cwd, env, max_bytes=8192)
    listing_result, listing = dotnet_graph.invoke([dotnet, '--list-sdks'], cwd, env, max_bytes=65536)
    if any(result['exit_code'] or result['reason'] for result in (version_result, listing_result)):
        return None
    version = version.strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', version):
        return None
    for line in listing.splitlines():
        match = re.fullmatch(r'([^ ]+) \[(.*)\]', line.strip())
        if match and match[1] == version:
            directory = Path(match[2]) / version
            if (directory / 'Microsoft.Build.dll').is_file():
                return version, directory
    return None


def _write(path, value):
    if path.exists():
        if path.read_text() != value:
            raise Conflict('solution reader bootstrap sources changed unexpectedly')
    else:
        # Publish complete bytes before a concurrent evaluator can read them.
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:
            temporary=Path(output.name)
            output.write(value.encode())
            output.flush()
            os.fsync(output.fileno())
        try:
            try:
                os.link(temporary,path)
            except FileExistsError:
                if path.read_text()!=value:
                    raise Conflict('solution reader bootstrap sources changed unexpectedly')
        finally:
            temporary.unlink(missing_ok=True)


def mapping(project, cwd, env, *, dotnet, configuration, framework=None, runtime=None, platform=None,
            artifacts_path=None, store=None):
    if store is None:
        return {'complete': False, 'reason': 'solution metadata requires broker support state'}
    selected = _sdk(cwd, env, dotnet)
    if selected is None:
        return {'complete': False, 'reason': 'selected SDK solution parser is unavailable'}
    version, sdk = selected
    inputs = [sdk, sdk.parent.parent / 'host', sdk.parent.parent / 'shared', sdk.parent.parent / 'packs']
    snapshot, _ = fingerprints.environment(Path(cwd), [dotnet],
        {'complete': True, 'executables': [], 'inputs': [str(path) for path in inputs if path.is_dir()], 'external_versions': {}}, env)
    if not snapshot['complete']:
        return {'complete': False, 'reason': 'SDK solution reader toolchain identity is incomplete'}
    key = digest({'source': SOURCE, 'sdk': snapshot['hash'], 'version': version})
    directory = store.root / 'state' / 'dotnet' / 'solution-reader' / key
    directory.mkdir(parents=True, exist_ok=True)
    refs = ''.join(f'<Reference Include="{name}"><HintPath>{escape(str(sdk / (name + ".dll")))}</HintPath><Private>true</Private></Reference>'
                   for name in ('Microsoft.Build', 'Microsoft.Build.Framework', 'Microsoft.Build.Utilities.Core'))
    project_source = f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
<TargetFramework>net{version.split('.')[0]}.0</TargetFramework><OutputType>Exe</OutputType>
<ImportDirectoryBuildProps>false</ImportDirectoryBuildProps><ImportDirectoryBuildTargets>false</ImportDirectoryBuildTargets>
<ManagePackageVersionsCentrally>false</ManagePackageVersionsCentrally><EnableDefaultItems>false</EnableDefaultItems>
<RestoreConfigFile>{escape(str(directory / 'NuGet.Config'))}</RestoreConfigFile>
</PropertyGroup><ItemGroup><Compile Include="Reader.cs"/>{refs}</ItemGroup></Project>'''
    _write(directory / 'Reader.cs', SOURCE)
    _write(directory / 'Reader.csproj', project_source)
    _write(directory / 'NuGet.Config', '<configuration><packageSources><clear /></packageSources></configuration>')
    _write(directory / 'global.json', json.dumps({'sdk': {'version': version, 'rollForward': 'disable', 'allowPrerelease': True}}, sort_keys=True))
    from . import dotnet_build, dotnet_artifacts
    bootstrap = dotnet_build.build(store, 'Reader.csproj', cwd=directory, env=env, dotnet=dotnet,
        configuration='Release', request_id='solution-reader-' + key,
        source_contract={'complete': True, 'inputs': ['Reader.cs', 'Reader.csproj', 'NuGet.Config', 'global.json']})
    if not bootstrap['valid']:
        return {'complete': False, 'reason': 'SDK solution reader bootstrap did not produce valid immutable outputs'}
    artifact = dotnet_build.artifact_metadata(store, bootstrap['build_id'])
    deployment = directory / 'deployment'
    if not deployment.exists():
        dotnet_artifacts.materialize(store, artifact, deployment)
    elif dotnet_artifacts.files(deployment) != artifact['manifest']:
        raise Conflict('deployed SDK solution reader differs from its immutable artifact')
    dll = next((deployment / node['properties']['TargetPath'].removeprefix(artifact['tree'] + '/')
                for node in artifact['graph']['nodes'] if node['properties']['TargetPath'].endswith('/Reader.dll')), None)
    if dll is None or not dll.is_file():
        return {'complete': False, 'reason': 'SDK solution reader executable is missing'}
    properties = {'Configuration': configuration, 'UseSharedCompilation': 'false', 'RestorePackagesPath': env['NUGET_PACKAGES']}
    for name, value in (('TargetFramework', framework), ('RuntimeIdentifier', runtime), ('Platform', platform)):
        if value is not None:
            properties[name] = value
    if artifacts_path is not None:
        properties.update(UseArtifactsOutput='true', ArtifactsPath=str(artifacts_path))
    # Input files are keyed by content; concurrent evaluations never share a
    # mutable request file or alter the immutable reader deployment.
    arguments = directory / ('input-' + digest(properties) + '.json')
    _write(arguments, json.dumps(properties, sort_keys=True))
    observed, text = dotnet_graph.invoke([artifact['graph']['resolved_dotnet'], str(dll), str(sdk), str(project), str(arguments)], cwd, env)
    try:
        value = json.loads(text)
    except ValueError:
        return {'complete': False, 'reason': 'SDK solution reader emitted invalid metadata'}
    if observed['exit_code'] or observed['reason'] or not value.get('complete'):
        return {'complete': False, 'reason': 'SDK solution graph evaluation failed: ' + str(value.get('reason', observed['reason']))}
    nodes = value.get('nodes')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= dotnet_graph.MAX_PROJECTS:
        return {'complete': False, 'reason': 'SDK solution graph exceeds its node bound or has no buildable nodes'}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('project'), str) or not Path(node['project']).is_absolute() or Path(node['project']).suffix.lower() not in ('.csproj', '.fsproj', '.vbproj'):
            return {'complete': False, 'reason': 'solution contains unsupported project types'}
        globals = node.get('global_properties')
        if not isinstance(globals, dict) or len(globals) > 128 or any(not isinstance(k,str) or not isinstance(v,str) or '\0' in k+v for k,v in globals.items()):
            return {'complete': False, 'reason': 'SDK solution project globals are invalid'}
        references = node.get('references')
        if not isinstance(references, list) or len(references) > dotnet_graph.MAX_PROJECTS:
            return {'complete': False, 'reason': 'SDK solution references are invalid'}
        if any(not isinstance(ref,dict) or not isinstance(ref.get('project'),str) or not isinstance(ref.get('global_properties'),dict) for ref in references):
            return {'complete': False, 'reason': 'SDK solution reference contexts are invalid'}
        references.sort(key=lambda ref: (ref['project'], digest(ref['global_properties'])))
    # ProjectGraph exposes a set. Its iteration order can vary per process and
    # must not cause a rebuild or look like an input mutation during compilation.
    nodes.sort(key=lambda node: (node['project'], digest(node['global_properties'])))
    value['reader_hash'] = key
    return value
