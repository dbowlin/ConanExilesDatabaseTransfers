# -*- mode: python ; coding: utf-8 -*-
"""Lean one-file build: Qt Widgets + sqlite3 only. No WebEngine/QML/tests/sqlite3.exe."""

_DENY = (
    'webengine', 'qt6webengine', 'qml', 'qt6qml', 'qtquick', 'qt6quick',
    'quick3d', '3dcore', '3drender', '3dinput', '3danimation', '3dextras',
    'multimedia', 'qt6multimedia', 'spatialaudio',
    'bluetooth', 'nfc', 'positioning', 'sensors', 'serialport', 'serialbus',
    'networkauth', 'remoteobjects', 'charts', 'datavisualization', 'graphs',
    'pdf', 'qt6pdf', 'texttospeech', 'webchannel', 'websockets', 'httpserver',
    'designer', 'help', 'qttest', 'statemachine', 'scxml', 'location',
    'virtualkeyboard', 'lottie', 'shadertools', 'qt63d', 'printsupport',
    'translations/', '.qm',
    'sqlite3.exe', 'screenshot.png', 'game.db', 'tests/',
)


def _kept(path):
    p = str(path).replace('\\', '/').lower()
    return not any(n in p for n in _DENY)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.'), ('icon.png', '.')],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'tkinter.test', '_tkinter',
        'unittest', 'unittest.mock', 'test', 'tests',
        'pydoc', 'doctest', 'xmlrpc', 'http.server',
        'numpy', 'PIL', 'cv2', 'pandas', 'pytest',
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
        'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSql',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtTextToSpeech', 'PySide6.QtWebChannel', 'PySide6.QtWebSockets',
        'PySide6.QtHelp', 'PySide6.QtDesigner', 'PySide6.QtTest',
        'PySide6.QtHttpServer', 'PySide6.QtRemoteObjects',
        'PySide6.QtStateMachine', 'PySide6.QtScxml',
        'PySide6.QtNetworkAuth', 'PySide6.QtLocation',
    ],
    noarchive=False,
)

a.binaries = [b for b in a.binaries if _kept(b[0]) and _kept(b[1])]
a.datas = [d for d in a.datas if _kept(d[0]) and _kept(d[1])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ConanExilesDBTransfer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
