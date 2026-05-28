import argparse
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BuildResult:
    ok: bool
    output: str


def run_cmd(cmd: list[str], cwd: Optional[Path] = None) -> BuildResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return BuildResult(ok=proc.returncode == 0, output=proc.stdout)


def normalize_repo_url(url: str) -> str:
    url = url.strip()
    # Support short GitHub notation like owner/repo.
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        return f"https://github.com/{url}.git"
    if url.startswith("https://github.com/") and not url.endswith(".git"):
        return f"{url}.git"
    return url


def clone_repo(repo_url: str, target_dir: Path) -> BuildResult:
    return run_cmd(["git", "clone", "--depth", "1", repo_url, str(target_dir)])


def detect_stack(repo_dir: Path) -> str:
    files = {p.name for p in repo_dir.iterdir() if p.is_file()}
    if "package.json" in files:
        return "node"
    if "requirements.txt" in files or "pyproject.toml" in files:
        return "python"
    if "pom.xml" in files or "build.gradle" in files:
        return "java"
    return "unknown"


def infer_run_command(stack: str, repo_dir: Path) -> str:
    if stack == "node":
        return 'CMD ["npm", "start"]'
    if stack == "python":
        if (repo_dir / "app.py").exists():
            return 'CMD ["python", "app.py"]'
        if (repo_dir / "main.py").exists():
            return 'CMD ["python", "main.py"]'
        return 'CMD ["python", "-m", "http.server", "8000"]'
    if stack == "java":
        return 'CMD ["java", "-jar", "app.jar"]'
    return 'CMD ["sh", "-c", "echo \'Set app start command\' && sleep 3600"]'


def generate_dockerfile(stack: str, repo_dir: Path, previous_error: str = "") -> str:
    run_cmd_line = infer_run_command(stack, repo_dir)
    if stack == "node":
        base = textwrap.dedent(
            f"""
            FROM node:20-alpine
            WORKDIR /app
            COPY package*.json ./
            RUN npm install
            COPY . .
            EXPOSE 3000
            {run_cmd_line}
            """
        ).strip()
    elif stack == "python":
        base = textwrap.dedent(
            f"""
            FROM python:3.11-slim
            WORKDIR /app
            COPY requirements.txt* ./
            RUN pip install --no-cache-dir --upgrade pip && \\
                if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
            COPY . .
            EXPOSE 8000
            {run_cmd_line}
            """
        ).strip()
    elif stack == "java":
        base = textwrap.dedent(
            f"""
            FROM eclipse-temurin:17-jre
            WORKDIR /app
            COPY . .
            EXPOSE 8080
            {run_cmd_line}
            """
        ).strip()
    else:
        base = textwrap.dedent(
            f"""
            FROM ubuntu:22.04
            WORKDIR /app
            COPY . .
            EXPOSE 8000
            {run_cmd_line}
            """
        ).strip()

    if "npm ERR! enoent" in previous_error:
        base = base.replace("RUN npm install", "RUN npm install --legacy-peer-deps || npm install")
    if "No matching distribution found" in previous_error:
        base = base.replace("python:3.11-slim", "python:3.10-slim")
    return base + "\n"


def build_image(repo_dir: Path, image_tag: str) -> BuildResult:
    return run_cmd(["docker", "build", "-t", image_tag, "."], cwd=repo_dir)


def run_container(image_tag: str, container_name: str, host_port: int, app_port: int) -> BuildResult:
    return run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "-d",
            "-p",
            f"{host_port}:{app_port}",
            image_tag,
        ]
    )


def verify_container(container_name: str) -> BuildResult:
    return run_cmd(["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Status}}"])


def read_docker_logs(container_name: str) -> BuildResult:
    return run_cmd(["docker", "logs", container_name])


def app_port_for_stack(stack: str) -> int:
    return {"node": 3000, "python": 8000, "java": 8080}.get(stack, 8000)


def main() -> None:
    parser = argparse.ArgumentParser(description="DockerForge - AI-Powered Dockerfile Generator (Python)")
    parser.add_argument("repo_url", help="Public GitHub repository URL or owner/repo")
    parser.add_argument("--max-retries", type=int, default=3, help="Max docker build retries")
    parser.add_argument("--host-port", type=int, default=8000, help="Host port for docker run")
    parser.add_argument("--image-tag", default="dockerforge-generated:latest", help="Docker image tag")
    args = parser.parse_args()

    repo_url = normalize_repo_url(args.repo_url)
    temp_root = Path(tempfile.mkdtemp(prefix="dockerforge_"))
    repo_dir = temp_root / "repo"
    container_name = f"dockerforge-{int(time.time())}"

    print(f"[1/7] Cloning repository: {repo_url}")
    clone = clone_repo(repo_url, repo_dir)
    if not clone.ok:
        print(clone.output)
        raise SystemExit("Failed to clone repository.")

    stack = detect_stack(repo_dir)
    port = app_port_for_stack(stack)
    print(f"[2/7] Detected stack: {stack}")

    build_output = ""
    dockerfile_text = ""
    last_error = ""

    for attempt in range(1, args.max_retries + 1):
        print(f"[3/7] Attempt {attempt}/{args.max_retries}: generating Dockerfile")
        dockerfile_text = generate_dockerfile(stack, repo_dir, previous_error=last_error)
        (repo_dir / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")

        print(f"[4/7] Attempt {attempt}/{args.max_retries}: docker build")
        build = build_image(repo_dir, args.image_tag)
        build_output = build.output
        if build.ok:
            print("Docker build succeeded.")
            break
        print("Docker build failed. Capturing error and retrying...")
        last_error = build.output
    else:
        print(build_output)
        raise SystemExit("Build failed after max retries.")

    print("[5/7] Running container")
    run_out = run_container(args.image_tag, container_name, args.host_port, port)
    if not run_out.ok:
        print(run_out.output)
        raise SystemExit("Failed to run container.")

    time.sleep(4)
    print("[6/7] Verifying container health")
    status = verify_container(container_name)
    logs = read_docker_logs(container_name)

    print("\n===== FINAL DOCKERFILE =====")
    print(dockerfile_text)
    print("===== BUILD OUTPUT (last attempt) =====")
    print(build_output)
    print("===== CONTAINER STATUS =====")
    print(status.output.strip() or "No running status found.")
    print("===== CONTAINER LOGS =====")
    print(logs.output.strip() or "No logs yet.")
    print(f"\n[7/7] Done. App should be available on http://localhost:{args.host_port}")

    # Keep cloned files for inspection if desired by setting this env var.
    if os.environ.get("DOCKERFORGE_KEEP_TEMP", "").lower() not in {"1", "true", "yes"}:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
