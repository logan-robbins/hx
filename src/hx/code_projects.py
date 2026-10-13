"""Manifest facts without invoking build tools."""

import json
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def extract(text, path):
    suffix = Path(path).suffix
    edges = []
    data = {}
    name = Path(path).stem
    try:
        if suffix == ".csproj":
            tree = ET.fromstring(text)
            packages = []
            for node in tree.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                value = node.get("Include", "")
                if tag == "ProjectReference" and value:
                    edges.append(
                        posixpath.normpath(
                            posixpath.join(
                                str(Path(path).parent), value.replace("\\", "/")
                            )
                        )
                    )
                if tag == "PackageReference":
                    edges.append("package:" + value + "@" + node.get("Version", ""))
                    packages.append({"name": value, "version": node.get("Version", "")})
            data = {
                "packages": packages,
                "test_project": any(
                    p["name"].lower() == "microsoft.net.test.sdk" for p in packages
                ),
            }
        elif suffix == ".sln":
            edges = [
                posixpath.normpath(
                    posixpath.join(str(Path(path).parent), m.replace("\\", "/"))
                )
                for m in re.findall(r'Project\([^\n]+?=\s*"[^"]+",\s*"([^"]+)"', text)
            ]
        elif Path(path).name == "package.json":
            package = json.loads(text)
            name = package.get("name", name)
            workspaces = package.get("workspaces", [])
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])
            data = {
                "workspaces": workspaces,
                "dependencies": package.get("dependencies", {}),
                "devDependencies": package.get("devDependencies", {}),
            }
            edges = list(data["dependencies"])
        else:
            return None, []
    except ET.ParseError, ValueError, AttributeError, TypeError:
        return None, []
    return dict(name=name, path=path, data=data), edges
