"""Runs the API tests and writes a JUnit XML report plus a Markdown summary.

Usage: python3 demo/tests/run.py <report-dir>
Exit code is non-zero when any test fails. On Buildkite it also posts the
summary as a build annotation.
"""
import os
import subprocess
import sys
import time
import unittest
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, os.path.dirname(__file__))


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._start = 0.0

    def startTest(self, test):
        self._start = time.time()
        super().startTest(test)

    def _record(self, test, outcome, detail=""):
        self.records.append((test, outcome, time.time() - self._start, detail))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))


def write_junit(records, path):
    failures = sum(1 for r in records if r[1] == "failed")
    errors = sum(1 for r in records if r[1] == "error")
    total_time = sum(r[2] for r in records)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<testsuite name="notes-api" tests="%d" failures="%d" errors="%d" time="%.3f">'
        % (len(records), failures, errors, total_time),
    ]
    for test, outcome, duration, detail in records:
        cls = "%s.%s" % (type(test).__module__, type(test).__name__)
        lines.append('  <testcase classname=%s name=%s file="demo/tests/test_api.py" time="%.3f">'
                     % (quoteattr(cls), quoteattr(test._testMethodName), duration))
        if outcome in ("failed", "error"):
            tag = "failure" if outcome == "failed" else "error"
            message = detail.strip().splitlines()[-1]
            lines.append('    <%s message=%s>%s</%s>' % (tag, quoteattr(message), escape(detail), tag))
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def write_summary(records, path):
    passed = [r for r in records if r[1] == "passed"]
    broken = [r for r in records if r[1] != "passed"]
    out = []
    if broken:
        out.append("### :x: API tests: %d of %d failed\n" % (len(broken), len(records)))
        for test, outcome, _, detail in broken:
            out.append("<details><summary><code>%s.%s</code></summary>\n"
                       % (type(test).__name__, test._testMethodName))
            out.append("\n```\n%s\n```\n</details>\n" % detail.strip())
    else:
        out.append("### :white_check_mark: API tests: all %d passed\n" % len(records))
    out.append("\n| Test | Result | Time |\n|---|---|---|")
    for test, outcome, duration, _ in records:
        icon = ":white_check_mark:" if outcome == "passed" else ":x:"
        out.append("| `%s.%s` | %s %s | %.0f ms |"
                   % (type(test).__name__, test._testMethodName, icon, outcome, duration * 1000))
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return len(passed), len(broken)


def main():
    report_dir = sys.argv[1] if len(sys.argv) > 1 else "demo/reports"
    os.makedirs(report_dir, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromName("test_api")
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    result = runner.run(suite)
    write_junit(result.records, os.path.join(report_dir, "junit.xml"))
    summary_path = os.path.join(report_dir, "summary.md")
    _, broken = write_summary(result.records, summary_path)
    annotate(summary_path, broken)
    sys.exit(1 if broken else 0)


def annotate(summary_path, broken):
    """Posts the summary to the build page. Skipped when not running in Buildkite."""
    if not os.environ.get("BUILDKITE"):
        return
    if broken:
        style = "error"
        note = (":lock: **Deploy to staging? is locked** because API tests failed. "
                "Fix the code and push. The new build unlocks the approval.")
    else:
        style = "success"
        note = (":unlock: **Next:** click the **:rocket: Deploy to staging?** step "
                "in the build graph, then click **Continue** to release.")
    with open(summary_path, "a") as fh:
        fh.write("\n" + note + "\n")
    with open(summary_path) as fh:
        subprocess.run(["buildkite-agent", "annotate", "--style", style, "--context", "api-tests"],
                       stdin=fh, check=False)


if __name__ == "__main__":
    main()
