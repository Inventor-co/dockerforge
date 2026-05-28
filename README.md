# DockerForge (Python)

AI-inspired Dockerfile generator that:
- Accepts a public GitHub repository URL
- Clones and analyzes project files
- Generates a Dockerfile
- Runs `docker build`, retries up to 3 times if it fails
- Runs container, verifies it starts, and prints logs/status
- Displays final working Dockerfile and command outputs

## Tech
- Python 3.10+
- Standard library only (`argparse`, `subprocess`, `tempfile`, etc.)
- Docker CLI and Git installed locally

## Project files
- `dockerforge.py`: main CLI app implementing the full assignment flow
- `requirements.txt`: dependency list (empty for this version)

## How to run
```bash
python dockerforge.py https://github.com/owner/repo