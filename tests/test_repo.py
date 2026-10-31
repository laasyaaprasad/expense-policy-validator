"""Repository guards that hold for every milestone."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_env_file_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored


def test_env_example_has_no_secrets():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.endswith("_API_KEY="):
            continue
        assert "_API_KEY=" not in line, f"value filled in: {line.split('=')[0]}"


def test_validator_never_imports_evaluation():
    # The pipeline must not be able to see the gold labels, which only evaluation/ reads.
    offenders = []
    for path in (ROOT / "validator").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "evaluation" or name.startswith("evaluation.") for name in names):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
