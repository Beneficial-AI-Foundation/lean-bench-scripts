#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from pathlib import Path


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def run_stdout(*command: str, cwd: str | None = None) -> str:
    result = subprocess.run(command, capture_output=True, encoding="utf-8", cwd=cwd)
    if result.returncode != 0:
        print(result.stdout, end="", file=sys.stdout)
        print(result.stderr, end="", file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout


def main() -> None:
    script_file = Path(__file__)
    template_file = script_file.parent / "lakeprof_report_template.html"

    # Host of the static report server (the BeneficialAIFoundation equivalent of
    # speed.lean-lang.org). Configured per-runner via the environment so this
    # script stays repo-agnostic. e.g. LAKEPROF_UPLOAD_BASE=https://speed.beneficialaifoundation.org
    host = os.environ.get("LAKEPROF_UPLOAD_BASE")
    if not host:
        print(
            "LAKEPROF_UPLOAD_BASE is not set; skipping lakeprof report upload.",
            file=sys.stderr,
        )
        return
    host = host.rstrip("/")

    # Derive the per-repo path segment from the repo-under-test's origin remote,
    # so a single runner serving multiple repos uploads each to its own subdir.
    # This basename matches the repo `name` in the radar config, which is what
    # determines the lakeprofReportUrl the UI links to.
    remote = run_stdout("git", "remote", "get-url", "origin").strip()
    repo = remote.rstrip("/").rsplit("/", 1)[-1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]

    sha = run_stdout("git", "rev-parse", "@").strip()
    base_url = f"{host}/{repo}/{sha}"
    report = run_stdout("lakeprof", "report", "-prc")
    with open(template_file) as f:
        template = f.read()

    template = template.replace("__BASE_URL__", json.dumps(base_url))
    template = template.replace("__LAKEPROF_REPORT__", report)

    with open("index.html", "w") as f:
        f.write(template)

    # The upload endpoint requires HTTP Basic Auth (writes only). Point
    # LAKEPROF_UPLOAD_NETRC at a 0600 netrc file holding the credentials so the
    # password is never passed on the command line (i.e. never visible in `ps`).
    # The netrc file should contain:
    #   machine <upload-host> login lakeprof password <token>
    auth = []
    netrc = os.environ.get("LAKEPROF_UPLOAD_NETRC")
    if netrc:
        auth = ["--netrc-file", netrc]

    run("curl", *auth, "-T", "index.html", f"{base_url}/index.html")
    run("curl", *auth, "-T", "lakeprof.log", f"{base_url}/lakeprof.log")
    run("curl", *auth, "-T", "lakeprof.trace_event", f"{base_url}/lakeprof.trace_event")


if __name__ == "__main__":
    main()
