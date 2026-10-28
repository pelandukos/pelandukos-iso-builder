"""Persistent boot visuals; never changes boot entries, kernel or /boot."""
import json
import re
from core.errors import BuilderError
from core.util import safe_path, read_text, atomic_json

CONFIG = 'etc/pelandukos-iso-builder/boot-appearance.json'
DEFAULTS = {'schema_version': 1, 'plymouth_mode': 'project', 'plymouth_theme': '',
            'grub_mode': 'managed', 'grub_directory': '',
            'logo': 'usr/share/plymouth/themes/pelandukos/pelandukos.png',
            'background': '#ffffff', 'foreground': '#18364d', 'accent': '#ed7615'}


def validate(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise BuilderError('Invalid boot appearance settings.')
    result = DEFAULTS | values
    if result['schema_version'] != 1:
        raise BuilderError('Unsupported boot appearance settings version.')
    for key, choices in [('plymouth_mode', ('project', 'managed')),
                         ('grub_mode', ('managed', 'custom', 'original'))]:
        if result[key] not in choices:
            raise BuilderError(f'Invalid {key}.')
    for key in ('background', 'foreground', 'accent'):
        if not isinstance(result[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            raise BuilderError('Colours must use #RRGGBB format.')
        result[key] = result[key].lower()
    name = result['plymouth_theme']
    if not isinstance(name, str) or (name and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name)):
        raise BuilderError('Invalid Plymouth theme name.')
    if result['plymouth_mode'] == 'managed' and name not in ('', 'pelandukos'):
        raise BuilderError('Managed Plymouth appearance uses the PelandukOS script theme.')
    for key in ('logo', 'grub_directory'):
        value = result[key]
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise BuilderError('Invalid boot asset path.')
        value = value.lstrip('/')
        if value:
            safe_path('/nonexistent', value)
        result[key] = value
    if result['grub_mode'] == 'custom' and not result['grub_directory']:
        raise BuilderError('Select the custom GRUB theme directory inside the ISO.')
    return result


def load(root):
    path = safe_path(root, CONFIG)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return validate(json.loads(read_text(path, 16384)))
    except (ValueError, TypeError) as error:
        raise BuilderError('Invalid saved boot appearance settings.') from error


def save(root, values):
    values = validate(values)
    # Validate every asset before replacing the single authoritative settings file.
    if values['plymouth_mode'] == 'managed' or values['grub_mode'] == 'managed':
        logo = safe_path(root, values['logo'])
        if not logo.is_file() or logo.stat().st_size > 16 * 1024**2:
            raise BuilderError('Choose a logo file inside the ISO, at most 16 MiB.')
    if values['grub_mode'] == 'custom':
        from core.boot_splash import custom_grub_assets
        custom_grub_assets(root, values['grub_directory'])
    if values['plymouth_theme']:
        theme = values['plymouth_theme']
        if not safe_path(root, f'usr/share/plymouth/themes/{theme}/{theme}.plymouth').is_file():
            raise BuilderError('The selected Plymouth theme is not installed in this project.')
    path = safe_path(root, CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, values)
    return values
