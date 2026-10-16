# Third-party notices / Fremdkomponenten

**Deutsch:** Dieses Repository enthält den eigenen Quellcode der FlyConomy-Desktop-Programme. Die unten genannten Bibliotheken liegen **nicht** im Repository; `pip` installiert sie beim Bauen, und PyInstaller packt sie in die fertigen EXE-Dateien. Die Liste nennt alles, was in den ausgelieferten Programmen steckt, mit Lizenz und Bezugsquelle. Die Lizenztexte liegen im Ordner [`licenses/`](licenses/) (kopiert aus den Paketdaten der Build-Umgebung), die AGPL in [`LICENSE`](LICENSE). Die Setups ab MiniClient 2.11.12.1 (und das damit neu verpackte Updater-Setup 1.0.5) installieren `LICENSE.txt`, diese Datei und den Ordner `licenses/` in den Programmordner.

**English:** This repository contains FlyConomy's own source code for the desktop programs. The libraries listed below are **not** part of the repository; `pip` installs them at build time and PyInstaller bundles them into the executables. The list covers everything that ships inside the distributed programs, with license and source. The license texts are in the [`licenses/`](licenses/) folder (copied from the package metadata of the build environment); the AGPL is in [`LICENSE`](LICENSE). The setups from MiniClient 2.11.12.1 onwards (and the Updater 1.0.5 setup repackaged with it) install `LICENSE.txt`, this file and the `licenses/` folder into the program folder.

Versions = build environment of the distributed programs (MiniClient 2.11.12 and 2.11.12.1, Updater 1.0.5, Browser inApp 1.0.1; see `build-constraints.txt`). The contents were checked by listing the files inside the distributed executables (2026-10-15; MiniClient 2.11.12.1: 2026-10-16).

## Why this program is AGPL-3.0 / Warum AGPL-3.0

The MiniClient and the full client import **Python-SimConnect** (`SimConnect` on PyPI), which is licensed under the GNU Affero General Public License v3 or later. The client programs are therefore distributed under the AGPL-3.0-or-later as well (see `LICENSE` and the license section in `README.md`, including the additional permission for `SimConnect.dll`).

## Python packages / Python-Pakete

| Package | Version | License | Source | Bundled in |
|---|---|---|---|---|
| SimConnect (Python-SimConnect), shipped unmodified | 0.4.26 | AGPL-3.0-or-later (package metadata: "AGPLv3+"); text: `LICENSE` | source of exactly this version: https://pypi.org/project/SimConnect/0.4.26/ (sdist); project: https://github.com/odwdinc/Python-SimConnect | MiniClient, full client |
| requests | 2.32.5 | Apache-2.0 | https://github.com/psf/requests | MiniClient, full client, Updater |
| urllib3 | 2.6.2 | MIT | https://github.com/urllib3/urllib3 | MiniClient, full client, Updater |
| idna | 3.11 | BSD-3-Clause | https://github.com/kjd/idna | MiniClient, full client, Updater |
| charset-normalizer | 3.4.4 | MIT | https://github.com/jawah/charset_normalizer | MiniClient, full client, Updater |
| certifi (`cacert.pem`) | 2025.11.12 | MPL-2.0 | https://github.com/certifi/python-certifi | MiniClient, full client, Updater |
| pystray | 0.19.5 | LGPL-3.0-or-later (texts: `licenses/pystray-0.19.5/COPYING` = GPL-3.0, `COPYING.LGPL` = LGPL-3.0) | https://github.com/moses-palmer/pystray | MiniClient, full client, Updater |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six | MiniClient, full client, Updater |
| Pillow (incl. statically linked libraries, see below) | 12.3.0 | MIT-CMU (HPND); bundled libraries under their own licenses | https://github.com/python-pillow/Pillow | MiniClient, full client, Updater |
| pypresence | 4.6.2 | MIT | https://github.com/qwertyquerty/pypresence | MiniClient, full client |
| typing_extensions | 4.15.0 | PSF-2.0 | https://github.com/python/typing_extensions | MiniClient, full client, Updater |
| pywebview (incl. `WebBrowserInterop.x64/x86.dll`) | 6.2.1 | BSD-3-Clause | https://github.com/r0x0r/pywebview | full client, Updater |
| bottle | 0.13.4 | MIT | https://github.com/bottlepy/bottle | full client, Updater |
| proxy_tools | 0.1.0 | MIT (package metadata; the package ships no license file) | https://github.com/jtushman/proxy_tools | full client, Updater |
| pythonnet (incl. `Python.Runtime.dll` and .NET Standard facade assemblies) | 3.1.0 | MIT | https://github.com/pythonnet/pythonnet | full client, Updater |
| clr_loader (incl. `ClrLoader.dll`) | 0.3.1 | MIT | https://github.com/pythonnet/clr-loader | full client, Updater |
| cffi | 2.0.0 | MIT | https://github.com/python-cffi/cffi | full client, Updater; MiniClient only `_cffi_backend` (*) |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser | full client, Updater |
| numpy (incl. `numpy.libs`, see below) | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; `numpy.libs` see below | https://github.com/numpy/numpy | MiniClient, full client, Updater (*) |
| cryptography | 46.0.3 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography | MiniClient, full client, Updater (*) |
| bcrypt | 5.0.0 | Apache-2.0 | https://github.com/pyca/bcrypt | MiniClient, full client, Updater (*) |
| psutil | 7.2.1 | BSD-3-Clause | https://github.com/giampaolo/psutil | MiniClient, full client, Updater (*) |
| setuptools (incl. `_distutils_hack` and the vendored packages below) | 80.9.0 | MIT | https://github.com/pypa/setuptools | MiniClient, full client, Updater (**) |
| packaging | 25.0 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging | MiniClient, full client, Updater (**) |

Vendored inside setuptools and bundled with it: backports.tarfile 1.2.0 (MIT), importlib_metadata 8.0.0 (Apache-2.0), jaraco.collections 5.1.0, jaraco.context 5.3.0, jaraco.functools 4.0.1, jaraco.text 3.12.1 (MIT), more_itertools 10.3.0 (MIT), packaging 24.2 (Apache-2.0 OR BSD-2-Clause), tomli 2.0.1 (MIT), wheel 0.45.1 (MIT), zipp 3.19.2 (MIT). Texts: `licenses/setuptools-80.9.0/_vendor/`.

(*) Not used by the FlyConomy code. PyInstaller picked these packages up because they were installed in the build environment (optional imports of other libraries). A build in a clean virtual environment with only `requirements.txt` and PyInstaller leaves numpy, cryptography, bcrypt and psutil out (and with them `_cffi_backend` in the MiniClient). For an exact rebuild of the distributed programs, install them with the versions from `build-constraints.txt`.

(**) Also not used by the FlyConomy code, but always present: PyInstaller itself depends on setuptools and packaging, so they are installed and bundled in a clean environment as well.

### Libraries inside Pillow / Bibliotheken in Pillow

The Pillow extension modules in the executables (`PIL\_imaging`, `_imagingcms`, `_webp`, `_avif`, `_imagingmath`, `_imagingtk`) contain statically linked third-party libraries, among them libjpeg-turbo, zlib-ng, libtiff, xz, OpenJPEG, Little CMS 2, libwebp and libavif with its codecs. Each is under its own license; Pillow's `LICENSE` (copied to `licenses/Pillow-12.3.0/LICENSE`) lists all of them with their full texts.

This software is based in part on the work of the Independent JPEG Group.

### Libraries inside numpy.libs / Bibliotheken in numpy.libs

`numpy.libs\libscipy_openblas64_*.dll` contains OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI) and the GCC runtime library libgfortran (GPL-3.0-or-later WITH GCC-exception-3.1). `numpy.libs\msvcp140-*.dll` is a copy of the Microsoft C++ runtime. Details and full texts: `licenses/numpy-2.4.6/LICENSE.txt` and the files next to it.

## Native components / Native Komponenten

| Component | License | Notes |
|---|---|---|
| CPython 3.14.0 runtime (`python314.dll`, `python3.dll`, standard library, extension modules) | PSF-2.0 | `licenses/CPython-3.14.0/LICENSE.txt` – also contains the licenses of libraries shipped with CPython for Windows (bzip2, libffi `libffi-8.dll`, OpenSSL 3 `libssl-3.dll`/`libcrypto-3.dll`, Zstandard and others) and the conditions for the Microsoft Distributable Code; overview: https://docs.python.org/3.14/license.html |
| Tcl/Tk 8.6 (`tcl86t.dll`, `tk86t.dll`, `_tcl_data`, `_tk_data`) | Tcl/Tk license (BSD-style) | MiniClient and full client; text: `licenses/CPython-3.14.0/Tcl-Tk-8.6-license.terms` |
| Microsoft Visual C++ runtime (`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `MSVCP140.dll`) and Universal C Runtime (`ucrtbase.dll`, `api-ms-win-*.dll`) | Microsoft Distributable Code | shipped with CPython for Windows; see "Additional Conditions for this Windows binary build" in the CPython license |
| `SimConnect.dll` (Microsoft Flight Simulator SDK), 58,368 bytes, SHA-256 `677fd3849d54e2a6831dc1aa348d974ead2255ec609fbf5830ef32bf0804b8bc` | **proprietary Microsoft software, not under the AGPL** | MiniClient and full client. Shipped unmodified, exactly as contained in the PyPI package SimConnect 0.4.26; the spec files copy it from the installed package. Microsoft has not confirmed its redistribution terms to FlyConomy. Not included in this repository. See the additional permission in `README.md`. |
| Microsoft WebView2 SDK (`Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`) | Microsoft WebView2 SDK license | shipped inside pywebview (full client, Updater); license: https://www.nuget.org/packages/Microsoft.Web.WebView2 |
| PyInstaller bootloader and loader modules (`pyiboot01_bootstrap`, `pyimod01_archive` … `pyimod04_pywin32`) | GPL-2.0-or-later WITH Bootloader-exception | `licenses/PyInstaller-6.17.0/COPYING.txt` – the exception allows distributing programs built with PyInstaller under their own license |
| PyInstaller run-time hooks (`pyi_rth_inspect`, `pyi_rth_pkgutil`, `pyi_rth_multiprocessing`, `pyi_rth__tkinter`, `pyi_rth_setuptools`) | Apache-2.0 | from PyInstaller 6.17.0; text in `licenses/PyInstaller-6.17.0/COPYING.txt` ("Run-time Hooks") |
| Run-time hook `pyi_rth_cryptography_openssl` | Apache-2.0 | from pyinstaller-hooks-contrib 2025.10; `licenses/pyinstaller-hooks-contrib-2025.10/LICENSE` |
| Inno Setup (setup and uninstaller stubs inside the `*_Setup.exe` files; distributed setups built with 6.7.0) | Inno Setup License | `licenses/InnoSetup-6/license.txt`, https://jrsoftware.org/isinfo.php |

## Loaded at runtime, not bundled / Zur Laufzeit geladen

| Component | License | Notes |
|---|---|---|
| Font Awesome Free 6.5.1 (CSS from cdnjs) | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT | only `ui/index.html` (local fallback page of the full client) |

## FlyConomy's own files / Eigene Dateien

All other files in this repository, including `logo.ico`, `logo.png`, `tray_icon.png`, `ui/phone/logo.png` and the icons in `ui/phone/icons/`, are FlyConomy's own work and are licensed under the AGPL-3.0-or-later like the code. The name "FlyConomy" and the logo are brand marks; the AGPL grants no trademark rights (AGPL section 7 e). See "Name und Logo / Name and logo" in `README.md`.
