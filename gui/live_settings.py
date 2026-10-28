"""Persistent controls for the next Live Desktop session."""
import json
from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QLabel, QSpinBox
from core.errors import BuilderError
from core.live_settings import DEFAULTS, RESOLUTIONS, validate_live_settings


class LiveSettings(QGroupBox):
    def __init__(self, settings, parent=None):
        super().__init__("Live Desktop hardware", parent)
        self.settings = settings
        try:
            saved = validate_live_settings(json.loads(settings.value("live_vm/settings", "null")))
        except (BuilderError, ValueError, TypeError):
            saved = dict(DEFAULTS)
        layout = QGridLayout(self)
        self.controls = {}
        specs = (("ram_gib", "RAM (GiB)", 1, 64), ("cpus", "vCPUs", 1, 64),
                 ("gpu_memory_mib", "GPU limit (MiB)", 128, 4096))
        for column, (key, label, minimum, maximum) in enumerate(specs):
            widget = QSpinBox()
            widget.setRange(minimum, maximum)
            widget.setValue(saved[key])
            self.controls[key] = widget
            layout.addWidget(QLabel(label), 0, column * 2)
            layout.addWidget(widget, 0, column * 2 + 1)
            widget.valueChanged.connect(self.save)
        for column, (key, label, options) in enumerate((
                ("gpu", "Graphics", (("Automatic", "auto"),
                                      ("Software", "software"), ("3D acceleration", "accelerated"))),
                ("resolution", "Resolution", tuple((r.replace("x", " × "), r) for r in RESOLUTIONS)))):
            widget = QComboBox()
            for text, value in options:
                widget.addItem(text, value)
            widget.setCurrentIndex(widget.findData(saved[key]))
            self.controls[key] = widget
            layout.addWidget(QLabel(label), 1, 0 if key == "gpu" else 4)
            layout.addWidget(widget, 1, 1 if key == "gpu" else 5, 1, 3 if key == "gpu" else 1)
            widget.currentIndexChanged.connect(self.save)
        self.setToolTip("Applies to the next Live Desktop session. Graphics memory is shared.")
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)
        for column in (1, 3, 5):
            layout.setColumnStretch(column, 1)

    def values(self):
        return validate_live_settings({key: widget.value() if isinstance(widget, QSpinBox) else widget.currentData()
                                       for key, widget in self.controls.items()})

    def save(self, *_):
        self.settings.setValue("live_vm/settings", json.dumps(self.values()))
