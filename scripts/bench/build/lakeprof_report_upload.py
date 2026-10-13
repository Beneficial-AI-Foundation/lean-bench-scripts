#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Column names for each table, in the order `lakeprof report --all -j` prints them.
PATH_FIELDS = ("time", "share", "cum_time", "cum_share", "module")
ALL_TABLES = {
    "critical_path": PATH_FIELDS,
    "rebuild_critical_path": PATH_FIELDS,
    "avg_contribution": PATH_FIELDS,
    "simulated_times": ("cpus", "time", "parallelism"),
}


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

    # Derive the per-repo path segment. The runner does not expose the radar
    # repo name as a string, but RADAR_CACHE is laid out as
    # <cache>/repos/<repo>/cache, so its parent dir is the canonical repo name
    # (the same `name` in the radar config that determines the
    # lakeprofReportUrl the UI links to). Fall back to the origin URL basename
    # for local runs executed against a normal clone, where RADAR_CACHE is
    # unset.
    cache_dir = os.environ.get("RADAR_CACHE")
    if cache_dir:
        repo = Path(cache_dir).parent.name
    else:
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

    # Full report bundle: everything `lakeprof report --all` produces, for download.
    # Its files are written to a temp dir under their own names (-c/-d) so the
    # Perfetto trace uploaded above is never touched.
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / f"{repo}-{sha}"
        bundle.mkdir()
        lines = run_stdout(
            "lakeprof", "report", "--all", "-j",
            "-c", str(bundle / "lakeprof-all.trace_event"),
            "-d", str(bundle / "lakeprof-all.dot"),
        ).splitlines()
        if len(lines) != len(ALL_TABLES):
            sys.exit(f"lakeprof report --all -j printed {len(lines)} tables, expected {len(ALL_TABLES)}")
        tables = {
            name: [dict(zip(fields, row, strict=True)) for row in json.loads(line)]
            for (name, fields), line in zip(ALL_TABLES.items(), lines)
        }
        (bundle / "lakeprof-all.json").write_text(json.dumps(tables, indent=2))
        shutil.copy("lakeprof.log", bundle)

        tarball = Path(tmp) / "lakeprof-all.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(bundle, arcname=bundle.name)
        run("curl", *auth, "-T", str(tarball), f"{base_url}/lakeprof-all.tar.gz")


if __name__ == "__main__":
    main()
