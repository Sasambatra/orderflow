"""Summarise actual JUnit and branch/statement coverage for GitHub Actions."""

import os
from pathlib import Path
from xml.etree import ElementTree


def main():
    coverage = ElementTree.parse("coverage.xml").getroot()
    covered = int(coverage.attrib["lines-covered"]) + int(coverage.attrib["branches-covered"])
    total = int(coverage.attrib["lines-valid"]) + int(coverage.attrib["branches-valid"])
    suites = ElementTree.parse("junit.xml").getroot().iter("testsuite")
    counts = {key: 0 for key in ["tests", "failures", "errors", "skipped"]}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.attrib.get(key, 0))
    summary = (
        "## Verification\n\n"
        f"- Tests: {counts['tests']}; failures: {counts['failures']}; errors: {counts['errors']}; skipped: {counts['skipped']}.\n"
        f"- Combined statement/branch coverage: {100 * covered / total:.2f}%.\n"
        "- Download the attached JUnit and coverage XML for details.\n"
    )
    print(summary)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a") as stream:
            stream.write(summary)


if __name__ == "__main__":
    main()
