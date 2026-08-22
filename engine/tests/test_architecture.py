import ast
from pathlib import Path


def test_domain_has_no_adapter_dependencies() -> None:
    root = Path(__file__).parents[1] / "src" / "soatvan"
    forbidden = ("lxml", "sqlite3", "zipfile", "tauri")
    for folder in (root / "checking", root / "workflow"):
        for file in folder.glob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(name.name for name in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            assert not any(item.startswith(forbidden) for item in imports), file
