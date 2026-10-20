"""What a built app may print on stderr in the end-to-end checks: nothing but a short allowlist."""

import re

# onnxruntime itself runs `sh -c "echo `blkid; hostname`"`; minimal images lack either command
NO_HOSTNAME = re.compile(r"^sh: .*(blkid|hostname): (command )?not found\n", re.MULTILINE)
TRACE_LINE = re.compile(r"^fsbpyr-trace .*\n?", re.MULTILINE)
# onnxruntime warns, with a timestamp, about the host's PCI devices on some VMs (GitHub's runners)
ONNX_HOST = re.compile(
    r"^\x1b\[0;93m\S+ \S+ \[W:onnxruntime:[^\]]* GetPciBusId\] .*\n?", re.MULTILINE
)


def noise(stderr: str, *allowed: re.Pattern[str]) -> str:
    """``stderr`` without the lines the check explicitly allows; empty when the app was quiet."""
    for pattern in allowed:
        stderr = pattern.sub("", stderr)
    return stderr
