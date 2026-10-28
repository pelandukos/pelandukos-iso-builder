import os
from types import SimpleNamespace

import pytest

from core.progress import Progress, ToolProgress, copy_stream, percentage_options
from core.util import digest
from services.image_storage import allocate, create_image, validate_image
from services.privileged_helper import UserFiles


def test_copy_and_checksum_measure_real_bytes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"x" * (10 * 1024**2))
    events = []
    with source.open("rb") as incoming, target.open("wb") as outgoing:
        copy_stream(incoming, outgoing, events.append, "Copy")
    assert [event["percent"] for event in events] == [0, 40, 80, 100]
    assert events[-1]["completed"] == source.stat().st_size
    hashes = []
    assert digest(target, progress=Progress(hashes.append, "Checksum")) == digest(source)
    assert hashes[-1]["percent"] == 100


def test_allocation_reports_completed_chunks_and_not_failed_completion(tmp_path, monkeypatch):
    events, chunks = [], []
    def reserve(fd, offset, size):
        chunks.append((offset, size))
        os.ftruncate(fd, offset + size)
    monkeypatch.setattr(os, "posix_fallocate", reserve)
    with (tmp_path / "image").open("w+b") as stream:
        allocate(stream.fileno(), 600 * 1024**2, lambda message: None, events.append)
    assert len(chunks) == 3
    assert [event["percent"] for event in events if event["type"] == "progress"] == [0, 42, 85, 100]
    assert events[-1]["type"] == "stage"  # flush is a separate, unmeasured stage
    events.clear()
    def full(*args):
        raise OSError("disk full")
    monkeypatch.setattr(os, "posix_fallocate", full)
    with (tmp_path / "failed").open("w+b") as stream, pytest.raises(OSError):
        allocate(stream.fileno(), 1024, lambda message: None, events.append)
    assert [event["percent"] for event in events] == [0]


def test_tool_progress_survives_arbitrary_chunks_and_ignores_statistics():
    events = []
    parser = ToolProgress(events.append, "Format", "mkfs")
    text = "Writing inode tables:  0/100\b\b 23/100\b\b 85/100\b done\nCreating journal (1 blocks): "
    for char in text:
        parser.feed(char)
    assert [event["percent"] for event in events if event["type"] == "progress"] == [0, 23, 85]
    assert events[-1]["message"] == "Formatting ext4: creating journal"
    events.clear()
    parser = ToolProgress(events.append, "Squash", "squashfs")
    parser.feed("size 25% of original\n2")
    parser.feed("5\n[======] 9/10 90%\r100\n")
    assert [event["percent"] for event in events if event["type"] == "progress"] == [25, 90, 100]
    events.clear()
    parser = ToolProgress(events.append, "ISO", "xorriso")
    parser.feed(" 12.50% done, estimate finish\nUPDATE : 80.00% done\n")
    assert [event["percent"] for event in events if event["type"] == "progress"] == [12, 80]


def test_older_squashfs_tools_do_not_receive_unsupported_option(monkeypatch):
    percentage_options.cache_clear()
    monkeypatch.setattr("core.progress.subprocess.run",
                        lambda *args, **kwargs: SimpleNamespace(stdout="-no-progress  disable bar\n"))
    try:
        assert percentage_options("unsquashfs") == []
    finally:
        percentage_options.cache_clear()


@pytest.mark.integration
def test_real_formatter_reports_stages_and_percentages(tmp_path):
    events = []
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        location = create_image(fd, UserFiles(os.getuid()), 128 * 1024**2,
                                lambda message: None, events.append)
    finally:
        os.close(fd)
    with open(location["path"], "rb") as stream:
        validate_image(stream.fileno(), location)
    assert any(event["type"] == "progress" and event["percent"] == 100
               and event["message"] == "Reserving exFAT container space" for event in events)
    assert any(event["type"] == "progress" and "inode tables" in event["message"] for event in events)
    assert events[-1]["message"] == "Flushing formatted container to disk"
