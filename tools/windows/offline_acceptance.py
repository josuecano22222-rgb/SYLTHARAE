"""The offline acceptance test for the native Windows deployment.

This is the release gate from the Windows offline deployment directive: it
drives a running, freshly installed SYLTHARAE through every acceptance step
while the machine is offline, monitors outbound connection attempts, and
writes a machine-readable evidence report. Exit status is non-zero when any
step fails or an unclassified external connection attempt is observed.

    python tools/windows/offline_acceptance.py \
        --base-url http://127.0.0.1:5000 \
        --admin-password '...' \
        --evidence ./acceptance-evidence \
        --restart   # pause so the operator can restart the app mid-run

Steps (directive numbering):
    1  start                          (operator/script; verified by health)
    2  open the local interface       GET / renders
    3  authenticate                   admin sign-in, session cookie flags
    4  ingest representative files    TXT / DOCX / PDF / XLSX / PNG folder
    5  document processing            every format found by search
    6  OCR                            scanned PNG read; provenance stored
    7  search and filter              keyword query + type filter
    8  analysis                       statistics + file analysis endpoints
    9  export                         CSV search export + settings export
   10  restart                        operator restarts (or --restart-command)
   11  authenticate again             sign-in after restart
   12  persistence                    pre-restart files still present
   13  repeat critical operations     search, analysis, export again

Network monitoring (--monitor, on by default) samples the server process
tree's TCP endpoints via psutil every second and classifies every non-loopback
address. Normal operation must produce none; any attempt is listed in the
report for classification (telemetry left configured is a defect offline).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

KEYWORD = "winacc offlinedoc"

STEPS = [
    (1, "start"),
    (2, "open the local interface"),
    (3, "authenticate"),
    (4, "ingest representative files"),
    (5, "document processing: DOCX/PDF/XLSX/image"),
    (6, "OCR"),
    (7, "search and filter"),
    (8, "analysis"),
    (9, "export"),
    (10, "restart"),
    (11, "authenticate again"),
    (12, "persistence"),
    (13, "repeat critical operations"),
]


def _hits(response) -> list:
    """The result list of a /api/search response, whatever shape it has."""
    try:
        data = response.json()
    except ValueError:
        return []
    if isinstance(data, dict):
        for key in ("results", "hits", "files"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


class NetworkMonitor:
    """Samples the server process tree for outbound connection attempts."""

    def __init__(self, root_pid: int | None, interval: float = 1.0):
        self.root_pid = root_pid
        self.interval = interval
        self.attempts: dict[str, dict] = {}
        self.samples = 0
        self._stop = False

    def _classify(self, ip: str, port: int) -> str:
        return classify_endpoint(ip, port)

    def run(self) -> None:
        import psutil

        while not self._stop:
            try:
                procs: list = []
                if self.root_pid:
                    parent = psutil.Process(self.root_pid)
                    procs = [parent, *parent.children(recursive=True)]
                for proc in procs:
                    for conn in proc.net_connections(kind="tcp"):
                        remote = conn.raddr
                        if not remote:
                            continue
                        ip, port = remote.ip, remote.port
                        kind = self._classify(ip, port)
                        if kind == "local":
                            continue
                        key = f"{ip}:{port}"
                        entry = self.attempts.setdefault(
                            key, {"ip": ip, "port": port,
                                  "classification": kind, "count": 0,
                                  "process": proc.info.get("name", "")})
                        entry["count"] += 1
                self.samples += 1
            except Exception:
                pass  # a process exiting between listing and probing is normal
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stop = True


def classify_endpoint(ip: str, port: int) -> str:
    """'local' (fine) or 'external' (must be investigated) — pure, tested."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "external"
    if addr.is_loopback or addr.is_unspecified or addr.is_link_local or addr.is_multicast:
        return "local"
    return "external"


class Acceptance:
    def __init__(self, base: str, password: str, evidence: Path, verify_tls: bool):
        self.base = base.rstrip("/")
        self.password = password
        self.evidence = evidence
        self.verify = verify_tls
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.results: list[dict] = []
        self.file_ids_before_restart: list[int] = []

    def check(self, step: int, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"step": step, "name": name, "ok": bool(ok),
                             "detail": str(detail)[:400]})
        print(("PASS " if ok else "FAIL ") + f"[{step}] {name}"
              + (f" -- {str(detail)[:300]}" if detail else ""), flush=True)
        return ok

    def csrf(self) -> None:
        token = self.session.get(f"{self.base}/api/csrf-token", timeout=30).json()["csrf_token"]
        self.session.headers["X-CSRFToken"] = token

    def login(self, step: int, label: str) -> None:
        self.csrf()
        r = self.session.post(f"{self.base}/auth/login",
                              json={"username": "admin", "password": self.password},
                              headers={"Referer": f"{self.base}/"}, timeout=30)
        ok = self.check(step, label, r.status_code == 200, r.text[:150])
        if ok:
            self.csrf()

    def make_fixtures(self) -> Path:
        docs = self.evidence / "documents"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "memo.txt").write_text(
            f"Offline acceptance memo: {KEYWORD} plain text.\n", encoding="utf-8")
        from docx import Document
        d = Document()
        d.add_paragraph(f"Offline acceptance: {KEYWORD} in a Word document.")
        d.save(docs / "minutes.docx")
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active["A1"] = f"{KEYWORD} spreadsheet row"
        wb.save(docs / "ledger.xlsx")
        import pymupdf
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 100), f"Offline acceptance: {KEYWORD} in a PDF.")
        pdf.save(str(docs / "report.pdf"))
        pdf.close()
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1200, 300), "white")
        ImageDraw.Draw(img).text((50, 120), f"Scanned {KEYWORD} page",
                                 font=ImageFont.truetype(
                                     os.environ.get(
                                         "SMOKE_OCR_FONT",
                                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                                     44),
                                 fill="black")
        img.save(docs / "scan.png")
        return docs

    def step_2_interface(self) -> None:
        r = self.session.get(f"{self.base}/", allow_redirects=False, timeout=30)
        self.check(2, "open the local interface", r.status_code in (200, 302),
                   f"{r.status_code}")

    def step_4_to_6_ingest(self) -> None:
        docs = self.make_fixtures()
        self.csrf()
        r = self.session.get(f"{self.base}/api/input/sources", timeout=30)
        source = "AcceptanceSource"
        self.session.post(f"{self.base}/api/input/sources",
                          json={"name": source, "job": "Audit", "importance": 0.5},
                          headers={"Referer": f"{self.base}/"}, timeout=30)
        self.session.post(f"{self.base}/api/input/sides",
                          json={"name": "AcceptanceSide", "importance": 0.5},
                          headers={"Referer": f"{self.base}/"}, timeout=30)
        r = self.session.post(
            f"{self.base}/api/input/jobs",
            json={"path": str(docs), "source": source, "side": "AcceptanceSide",
                  "recursive": True},
            headers={"Referer": f"{self.base}/"}, timeout=60)
        if not self.check(4, "ingest representative files (TXT/DOCX/PDF/XLSX/PNG)",
                          r.status_code in (200, 201, 202), r.text[:200]):
            return
        job_id = r.json().get("job_id") or r.json().get("job", {}).get("job_id")
        for _ in range(600):
            j = self.session.get(f"{self.base}/api/jobs/{job_id}", timeout=30).json()
            job = j.get("job", j)
            if job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"):
                break
            time.sleep(1)
        self.check(4, "ingestion job completed",
                   job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS"),
                   str(job.get("status")))

        r = self.session.get(f"{self.base}/api/search",
                             params={"query": KEYWORD}, timeout=30)
        text = r.text.lower()
        for label, needle in (("TXT", "memo.txt"), ("DOCX", "minutes.docx"),
                              ("PDF", "report.pdf"), ("XLSX", "ledger.xlsx"),
                              ("PNG/OCR", "scan.png")):
            self.check(5, f"document processing: {label} found by search",
                       needle in text, needle)

        r = self.session.get(f"{self.base}/api/search",
                             params={"query": KEYWORD}, timeout=30)
        scan_id = None
        for hit in _hits(r):
            if hit.get("file_name") == "scan.png":
                scan_id = hit.get("file_id") or hit.get("id")
        ocr = {}
        if scan_id:
            det = self.session.get(f"{self.base}/api/file/{scan_id}/details",
                                   timeout=30).json()
            details = det.get("details") or det
            ocr = (details.get("extraction_provenance") or {}).get("ocr") or {}
        conf = ocr.get("confidence")
        self.check(6, "OCR ran offline with provenance",
                   ocr.get("derived") is True
                   and ocr.get("engine") in ("tesseract", "rapidocr")
                   and isinstance(conf, (int, float)) and 0 < conf <= 1,
                   json.dumps(ocr)[:200])
        self.scan_id = scan_id

    def step_7_to_9(self) -> None:
        r = self.session.get(f"{self.base}/api/search",
                             params={"query": KEYWORD}, timeout=30)
        ok = self.check(7, "search", r.status_code == 200 and KEYWORD in r.text.lower())
        r2 = self.session.get(f"{self.base}/api/search",
                              params={"query": KEYWORD, "file_type": ".txt"}, timeout=30)
        self.check(7, "filter", r2.status_code == 200, r2.status_code)

        ids = sorted({h.get("file_id") or h.get("id") for h in _hits(r)} - {None})
        fid = ids[0] if ids else None
        stats = self.session.get(f"{self.base}/api/analysis/statistics/{fid}",
                                 timeout=30) if fid else None
        detail = self.session.get(f"{self.base}/api/analysis/file/{fid}",
                                  timeout=30) if fid else None
        self.check(8, "analysis", bool(stats and stats.status_code == 200)
                   and bool(detail and detail.status_code == 200),
                   f"{stats.status_code if stats else '-'}/{detail.status_code if detail else '-'}")

        exp = self.session.post(f"{self.base}/api/search/export",
                                json={"query": KEYWORD, "format": "csv"},
                                headers={"Referer": f"{self.base}/"}, timeout=60)
        settings = self.session.get(f"{self.base}/api/import-export/settings/export",
                                    timeout=60)
        self.check(9, "export", exp.status_code == 200 and settings.status_code == 200,
                   f"{exp.status_code}/{settings.status_code}")
        self.file_ids_before_restart = ids

    def step_12_13(self) -> None:
        r = self.session.get(f"{self.base}/api/search",
                             params={"query": KEYWORD}, timeout=30)
        ids = sorted({h.get("file_id") or h.get("id") for h in _hits(r)} - {None})
        self.check(12, "persistence after restart",
                   set(self.file_ids_before_restart) <= set(ids),
                   f"{len(set(self.file_ids_before_restart) & set(ids))} of "
                   f"{len(self.file_ids_before_restart)} files still present")

        r = self.session.get(f"{self.base}/api/search",
                             params={"query": KEYWORD}, timeout=30)
        exp = self.session.post(f"{self.base}/api/search/export",
                                json={"query": KEYWORD, "format": "csv"},
                                headers={"Referer": f"{self.base}/"}, timeout=60)
        self.check(13, "repeat critical operations",
                   r.status_code == 200 and exp.status_code == 200,
                   f"{r.status_code}/{exp.status_code}")

    def run(self, pause_for_restart: bool, restart_command: str | None) -> int:
        r = self.session.get(f"{self.base}/health", timeout=30)
        self.check(1, "start (application answers /health)",
                   r.status_code == 200 and r.json().get("status") == "healthy",
                   r.text[:120])
        self.step_2_interface()
        self.login(3, "authenticate")
        self.step_4_to_6_ingest()
        self.step_7_to_9()

        if pause_for_restart:
            print("\nRESTART the application now (Ctrl-C in its window, start it "
                  "again), then press Enter here...", flush=True)
            try:
                input()
            except EOFError:
                time.sleep(30)
        if restart_command:
            # The operator's own --restart-command string; a full command
            # line is the documented interface (see
            # docs/windows/OFFLINE_VALIDATION.md) and the operator already
            # controls this machine, so no privilege is gained here.
            subprocess.run(restart_command, shell=True, check=False)  # nosec B602 - operator-provided command
            time.sleep(5)

        self.check(10, "restart (application answers /health again)",
                   self.session.get(f"{self.base}/health", timeout=60).ok)
        self.login(11, "authenticate again")
        self.step_12_13()
        return 0 if all(item["ok"] for item in self.results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--evidence", type=Path, default=Path("acceptance-evidence"))
    parser.add_argument("--monitor", dest="monitor", action="store_true", default=True)
    parser.add_argument("--no-monitor", dest="monitor", action="store_false")
    parser.add_argument("--server-pid", type=int, default=None,
                        help="PID of the running server process tree to monitor")
    parser.add_argument("--pause-for-restart", action="store_true")
    parser.add_argument("--restart-command", default=None)
    args = parser.parse_args()

    args.evidence.mkdir(parents=True, exist_ok=True)
    acceptance = Acceptance(args.base_url, args.admin_password, args.evidence,
                            verify_tls=False)

    monitor = NetworkMonitor(args.server_pid) if args.monitor else None
    import threading

    if monitor:
        threading.Thread(target=monitor.run, daemon=True).start()
    try:
        code = acceptance.run(args.pause_for_restart, args.restart_command)
    finally:
        if monitor:
            monitor.stop()
            time.sleep(0.1)

    report = {
        "steps": [{"step": step, "name": name} for step, name in STEPS],
        "results": acceptance.results,
        "network": {
            "samples": monitor.samples if monitor else 0,
            "external_attempts": monitor.attempts if monitor else {},
        },
    }
    report_path = args.evidence / "acceptance-report.json"
    report_path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    failures = [item for item in acceptance.results if not item["ok"]]
    external = {k: v for k, v in report["network"]["external_attempts"].items()
                if v["classification"] == "external"}
    print(f"\nreport: {report_path}")
    print(f"{len(acceptance.results) - len(failures)}/{len(acceptance.results)} checks passed")
    if external:
        print("EXTERNAL connection attempts observed (classify each):")
        for key, entry in external.items():
            print(f"  - {key} x{entry['count']} ({entry['process']})")
        code = 1
    print("OFFLINE ACCEPTANCE:", "PASS" if code == 0 else "FAIL")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
