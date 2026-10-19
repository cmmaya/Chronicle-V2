# Replaces the pyinstaller-hooks-contrib hook, which copies the metadata of
# the "webrtcvad" distribution. Chronicle installs "webrtcvad-wheels", and
# webrtcvad.py reads its version from that name.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
