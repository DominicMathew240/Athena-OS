"""OSS hygiene: files that must not ship, and the documented env example."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    "memory/",
    ".emergent/",
)


def _tracked_files() -> list[str]:
    git_dir = REPO / ".git"
    if not git_dir.exists():
        return []
    import subprocess

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_forbidden_paths_are_not_tracked():
    tracked = _tracked_files()
    if not tracked:
        return
    leaked = [path for path in tracked if path.startswith(FORBIDDEN_PREFIXES)]
    assert leaked == [], leaked


def test_env_example_exists_and_has_no_secret_values():
    path = REPO / "web" / ".env.example"
    assert path.is_file(), "web/.env.example is required for a clean install"
    text = path.read_text()
    assert "DJANGO_SECRET_KEY=" in text
    assert "ATHENA_ALLOWED_HOSTS=" in text
    assert "ATHENA_TRAINING_ALLOW_DOWNLOAD=0" in text
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in {"DJANGO_SECRET_KEY", "ATHENA_VLM_API_KEY", "ATHENA_S3_SECRET_ACCESS_KEY"}:
            assert value.strip() == "", key
