"""A test project that installs: a pyproject.toml with a dependency-free in-tree PEP 660 backend, and a setup.sh."""
import textwrap
from pathlib import Path

BACKEND = textwrap.dedent('''
    """A dependency-free PEP 660 backend: the editable wheel holds one .pth file pointing at the project."""
    import base64, hashlib, os, zipfile
    NAME, VERSION = "toolpkg", "0.1.0"
    DIST = f"{NAME}-{VERSION}.dist-info"
    META = f"Metadata-Version: 2.1\\nName: {NAME}\\nVersion: {VERSION}\\n"
    def get_requires_for_build_editable(config_settings=None):
        return []
    def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
        os.makedirs(os.path.join(metadata_directory, DIST))
        with open(os.path.join(metadata_directory, DIST, "METADATA"), "w") as f:
            f.write(META)
        return DIST
    def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
        files = {f"{NAME}.pth": os.path.dirname(os.path.abspath(__file__)) + "\\n", f"{DIST}/METADATA": META,
                 f"{DIST}/WHEEL": "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"}
        name, record = f"{NAME}-{VERSION}-py3-none-any.whl", []
        with zipfile.ZipFile(os.path.join(wheel_directory, name), "w") as z:
            for path, text in files.items():
                data = text.encode()
                z.writestr(path, data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                record.append(f"{path},sha256={digest},{len(data)}")
            z.writestr(f"{DIST}/RECORD", "\\n".join(record + [f"{DIST}/RECORD,,"]) + "\\n")
        return name
''')
PYPROJECT = ('[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n\n'
             '[project]\nname = "toolpkg"\nversion = "0.1.0"\n')
SETUP_SH = "#!/bin/sh\nset -e\ncd \"$(dirname \"$0\")\"\npip install -e . --no-build-isolation\n"


def installable(p: Path) -> None:
    """Give the project a pyproject.toml whose (dependency-free, in-tree) backend installs it, and a setup.sh."""
    (p / "backend.py").write_text(BACKEND)
    (p / "pyproject.toml").write_text(PYPROJECT)
    (p / "setup.sh").write_text(SETUP_SH)
