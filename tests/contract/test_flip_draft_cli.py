import subprocess
import sys


def test_flip_draft_cli_rewrites_file(tmp_path):
    p = tmp_path / "post.md"
    p.write_text("---\ndraft: true\n---\nbody\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "src.tools.flip_draft", str(p)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = p.read_text(encoding="utf-8")
    assert "draft: false" in out
    assert "draft: true" not in out


def test_flip_draft_cli_missing_file_is_noop(tmp_path):
    missing = tmp_path / "nope.md"
    r = subprocess.run(
        [sys.executable, "-m", "src.tools.flip_draft", str(missing)],
        capture_output=True, text=True)
    assert r.returncode == 0          # 缺文件不报错(workflow 容错)
