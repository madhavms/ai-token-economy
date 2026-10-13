"""Shrink test-runner output to what a model needs: failures with a little context, plus the summary.

Used by the pre_tool hook:  <test command> 2>&1 | python trim.py
Short output passes through untouched. Stdlib only, Python 3.8+.
"""
import re
import sys

KEEP = re.compile(r"(FAIL|ERROR|Error|error[:\[]|Traceback|AssertionError|assert |panicked|Exception|"
                  r"✗|✕|FAILED|failures?:|not ok)")
PASS_THROUGH = 150   # lines
BEFORE, AFTER, TAIL, CAP = 2, 6, 25, 220


def trim(lines):
    if len(lines) <= PASS_THROUGH:
        return lines
    keep = set(range(max(0, len(lines) - TAIL), len(lines)))
    for i, line in enumerate(lines):
        if KEEP.search(line):
            keep.update(range(max(0, i - BEFORE), min(len(lines), i + AFTER + 1)))
    out, last = [], -2
    for i in sorted(keep):
        if i != last + 1:
            out.append("...")
        out.append(lines[i])
        last = i
    if len(out) > CAP:
        out = out[:CAP - TAIL] + ["..."] + out[-TAIL:]
    return ["[tke: showing %d of %d lines; failures and the summary kept]" % (len(out), len(lines))] + out


def main():
    data = sys.stdin.read().splitlines()
    sys.stdout.write("\n".join(trim(data)) + ("\n" if data else ""))


if __name__ == "__main__":
    main()
