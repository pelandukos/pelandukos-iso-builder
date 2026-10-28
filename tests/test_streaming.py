import sys

import pytest

from core.errors import BuilderError
from core.iso_extractor import SquashFSSizeEstimator
from services.commands import CommandRunner


def test_large_command_output_can_be_reduced_without_capture():
    total = 0
    def count(text):
        nonlocal total
        total += len(text)
    result = CommandRunner().run(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * (17 * 1024**2))"],
        capture=False, output_handler=count, log_output=False,
    )
    assert result == ""
    assert total == 17 * 1024**2


def test_streaming_errors_still_report_diagnostics():
    with pytest.raises(BuilderError, match="bad archive"):
        CommandRunner().run([sys.executable, "-c", "import sys; print('bad archive'); sys.exit(1)"],
                            capture=False, log_output=False)


def test_listing_estimate_handles_chunk_boundaries():
    estimator = SquashFSSizeEstimator()
    text = ("header\n"
            "drwxr-xr-x 0/0 100 2026-01-01 00:00 squashfs-root\n"
            "-rw-r--r-- 1000/1000 200 2026-01-01 00:00 squashfs-root/file\n"
            "lrwxrwxrwx 0/0 20 2026-01-01 00:00 squashfs-root/link -> /some/target")
    for offset in range(0, len(text), 7):
        estimator.feed(text[offset:offset + 7])
    assert estimator.finish() == 320
    assert estimator.entries == 3


def test_listing_estimate_does_not_accept_empty_or_malformed_output():
    with pytest.raises(BuilderError, match="Cannot estimate"):
        SquashFSSizeEstimator().finish()
    with pytest.raises(BuilderError, match="long record"):
        SquashFSSizeEstimator().feed("a" * 65537)
