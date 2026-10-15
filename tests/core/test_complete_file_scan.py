"""Size discovery examines complete large files without allocating their bodies."""
import hashlib
import subprocess

from hx import file_limits


def test_complete_outlier_scan_streams_and_preserves_split_unicode(tmp_path, monkeypatch):
    from hx import code_store
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    # UTF-8 sequence crosses the scanner's internal I/O boundary. These
    # internal chunks never become model-visible partial file reads.
    data = b'a' * 65535 + '🦉'.encode() + b'z' * 100000 + b'\nexisting_provider_at_end = True\n'
    (tmp_path / 'outlier.py').write_bytes(data)
    def no_whole_allocation(path):
        raise AssertionError('size scans must stream regular source files')
    monkeypatch.setattr(code_store, 'source_bytes', no_whole_allocation)
    result = file_limits.scan_repo(tmp_path)
    assert result['complete'] and result['files_scanned'] == 1
    assert result['outliers'] == [{'path': 'outlier.py', 'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(), 'estimated_tokens': (len(data) + 3) // 4, 'oversized': True}]


def test_invalid_utf8_tail_is_reported_as_binary_not_a_valid_prefix(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'incomplete.py').write_bytes(b'a' * 65536 + b'\xe2\x82')
    result = file_limits.scan_repo(tmp_path)
    assert result['complete'] and result['binary_paths'] == ['incomplete.py']
    assert result['files_scanned'] == 0
