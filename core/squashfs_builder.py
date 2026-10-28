from core.progress import ToolProgress, stage, percentage_options


class SquashFSBuilder:
    def __init__(self, sandbox, emit=lambda event: None):
        self.sandbox = sandbox
        self.emit = emit

    def build(self, root, output_directory):
        progress = ToolProgress(self.emit, "Compressing Linux root filesystem", "squashfs")
        self.sandbox.run(
            ["mksquashfs", "/rootfs", "/out/filesystem.squashfs",
             "-noappend", "-comp", "xz", "-processors", "2", "-mem", "512M",
             "-xattrs", "-exit-on-error", *percentage_options("mksquashfs")],
            reads={root: "/rootfs"}, writes={output_directory: "/out"},
            output_handler=progress.feed, capture=False,
        )
        stage(self.emit, "Checking the rebuilt SquashFS")
        self.sandbox.run(["unsquashfs", "-s", "/filesystem.squashfs"],
                         reads={output_directory / "filesystem.squashfs": "/filesystem.squashfs"})
        return output_directory / "filesystem.squashfs"
