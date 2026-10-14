"""Actual CI manifest command pins Git source and tested artifact bytes."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hx import ci_manifest
from hx.continuity_store import Conflict
from hx.errors import ValidationError
from .test_appmap import git


def test_ci_manifest_command_records_real_source_and_exact_artifact(tmp_path):
    repo=tmp_path/'repo'
    repo.mkdir()
    git(repo,'init','-b','main')
    git(repo,'config','user.email','test@example.com')
    git(repo,'config','user.name','Test')
    (repo/'source.py').write_text('value = 1\n')
    git(repo,'add','source.py')
    git(repo,'commit','-m','fixed source')
    head=git(repo,'rev-parse','HEAD')
    (repo/'product.bin').write_bytes(b'tested product')
    output=tmp_path/'manifest.json'
    result=subprocess.run([sys.executable,'-m','hx.ci_manifest','--repo',str(repo),'--expected-head',head,
        '--artifact','product.bin','--check','integration=success','--check','installed=passed','--out',str(output)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    manifest=json.loads(output.read_text())
    assert manifest['source_commit']==head
    assert manifest['source_tree']==git(repo,'rev-parse','HEAD^{tree}')
    assert manifest['artifacts']['product.bin']['sha256']==hashlib.sha256(b'tested product').hexdigest()
    assert json.loads(result.stdout)['build_id']==manifest['build_id']
    with pytest.raises(ValidationError):
        ci_manifest.manifest(repo,['product.bin'],{'integration':'failure'},expected_head=head)
    (repo/'source.py').write_text('value = 2\n')
    with pytest.raises(Conflict):
        ci_manifest.manifest(repo,['product.bin'],{'integration':'success'},expected_head=head)


def test_ci_preserves_required_full_tests_and_fixed_candidates_are_not_cancelled():
    root=Path(__file__).resolve().parents[2]
    actual=(root/'.github/workflows/ci.yml').read_text()
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in actual
    assert 'github.event.pull_request.number || github.sha' in actual
    assert '.venv/bin/python -m pytest -q' in actual
    assert 'needs: test' in actual and 'python -m hx.ci_manifest' in actual
    fixed=(root/'src/hx/skeleton/templates/ci/fixed-validation.yml').read_text()
    assert 'cancel-in-progress: false' in fixed and 'group: hx-fixed-${{ inputs.batch }}' in fixed
    assert 'hx change run-validation "$HX_BATCH"' in fixed
