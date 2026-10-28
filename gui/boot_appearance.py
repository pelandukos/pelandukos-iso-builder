from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QDialogButtonBox, QLabel, QPushButton, QHBoxLayout, QColorDialog,
    QWidget, QMessageBox)
from core.boot_appearance import validate
from core.errors import BuilderError


class BootAppearanceDialog(QDialog):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Boot appearance')
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        note = QLabel('Saved with this project and applied whenever you build an ISO.\n'
                      'Asset paths refer to files inside the ISO project. Import images using File Manager.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout();layout.addLayout(form)
        self.fields = {}
        choices = {
            'plymouth_mode': [('Use current project theme', 'project'), ('PelandukOS colours and logo', 'managed')],
            'grub_mode': [('PelandukOS colours and logo', 'managed'), ('Custom GRUB theme directory', 'custom'),
                          ('Keep source ISO menu', 'original')]}
        labels = {'plymouth_mode':'Bootsplash style', 'plymouth_theme':'Plymouth theme (blank = current)',
                  'grub_mode':'GRUB style', 'grub_directory':'Custom GRUB directory', 'logo':'Logo path inside ISO',
                  'background':'Background', 'foreground':'Text colour', 'accent':'Accent colour'}
        for key, label in labels.items():
            if key in choices:
                field = QComboBox()
                for text,value in choices[key]:field.addItem(text,value)
                field.setCurrentIndex(field.findData(values[key]))
                form.addRow(label,field)
            else:
                field = QLineEdit(values[key]);field.setMaxLength(512)
                if key in ('background','foreground','accent'):
                    box=QWidget();row=QHBoxLayout(box);row.setContentsMargins(0,0,0,0);row.addWidget(field)
                    button=QPushButton('Choose…');row.addWidget(button)
                    button.clicked.connect(lambda checked=False,f=field:self.choose_colour(f))
                    form.addRow(label,box)
                else:form.addRow(label,field)
            self.fields[key]=field
        self.fields['grub_mode'].currentIndexChanged.connect(self.refresh)
        self.refresh()
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def refresh(self):
        self.fields['grub_directory'].setEnabled(self.fields['grub_mode'].currentData()=='custom')

    def choose_colour(self,field):
        from PySide6.QtGui import QColor
        colour=QColorDialog.getColor(QColor(field.text()),self)
        if colour.isValid():field.setText(colour.name())

    def values(self):
        return validate({key:field.currentData() if isinstance(field,QComboBox) else field.text().strip()
                         for key,field in self.fields.items()})

    def accept(self):
        try:self.values()
        except BuilderError as error:
            QMessageBox.warning(self,'Boot appearance',str(error));return
        super().accept()
