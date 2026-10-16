# FlyConomy Client

**Deutsch** | [English](#english)

Quellcode der Windows-Programme von [FlyConomy](https://flyconomy.de), einer Wirtschaftssimulation für den Microsoft Flight Simulator (MSFS 2020/2024).

| Programm | Datei | Aufgabe |
|---|---|---|
| **MiniClient** (Hauptprogramm) | `flyconomy_miniclient.py` → `flyconomy_client.py` | Läuft im Infobereich (Tray). Liest den Simulator über SimConnect, startet und beendet Flüge bei FlyConomy, stellt die lokale Schnittstelle `http://127.0.0.1:17474` für das In-Game-Panel bereit und verbindet sich mit der Website. |
| **Voller Client** (eingestellt) | `flyconomy_client.py` (ohne `--mini`) | gleicher Code wie der MiniClient, zusätzlich mit Fenster (pywebview), das flyconomy.de anzeigt |
| **Updater** | `flyconomy_updater.py` | installiert und aktualisiert MiniClient und MSFS-Panel, legt die Desktop-Verknüpfung „FlyConomy“ an |
| **Browser inApp** (WebApp) | `flyconomy_webapp.py` | öffnet flyconomy.de im Standardbrowser als eigenes App-Fenster |

Hilfsmodule: `client_gateway.py` (WebSocket zur Website), `client_notice.py` (Hinweisfenster, Problemmeldung), `flight_log_ui.py` (Fenster „Flug wird geloggt“), `sim_ground.py` (Bodenfahrzeuge, Hotkeys), `payware_payload.py` (Beladung von Payware-Flugzeugen), `cockpit_lamp_test.py` (Lampentest als Quittung), `discord_presence.py` (Discord-Statusanzeige). `ui/` enthält die Seiten der Programme. `ui/phone/` ist eine Kopie der Panel-Oberfläche: Der MiniClient zeigt sie unter `http://127.0.0.1:17474/phone/` als Panel-Ansicht im eigenen Fenster auf dem PC (z. B. für einen Zweitbildschirm).

### Was hier nicht enthalten ist

Der **FlyConomy-Server, die Website, die Datenbank und der Betrieb** gehören **nicht** zu diesem Repository und sind nicht veröffentlicht. Die Programme sprechen mit dem Dienst unter `https://flyconomy.de/api`. Dafür braucht man ein FlyConomy-Konto (Anmeldung über Discord). Das MSFS-In-Game-Panel ist ein eigener Download; hier liegt nur die Kopie seiner Oberfläche in `ui/phone/`.

### Versionen

Dieser Stand (Tag `v2.11.12.1`) ist der Quellcode der Programme, die auf flyconomy.de als Sicherheits-Hotfix (Nachfolger der 2.11.12) ausgeliefert werden. Welche Version gerade angeboten wird, steht auf [flyconomy.de/downloads](https://flyconomy.de/downloads). Der Entwicklungsstand und spätere Versionen liegen im Branch `main` und in weiteren Tags. Bitte immer die aktuelle Version verwenden; ältere Versionen bekommen keine Fehler- und Sicherheitskorrekturen.

| Programm | Version | Build | Quellstand |
|---|---|---|---|
| MiniClient | 2.11.12.1 | 16.10.2026 | dieser Stand, geprüft (siehe unten) |
| Updater | 1.0.5 | 30.08.2026, Setup neu verpackt 16.10.2026 | dieser Stand, geprüft |
| Browser inApp | 1.0.1 | 22.08.2026 | dieser Stand bis auf sechs Zeilen, geprüft (siehe unten) |
| Panel-Kopie `ui/phone` | 1.4.30 | – | dieser Stand |
| Voller Client | 2.11.4 | 19.08.2026 | **nicht erhalten**, wird nicht mehr angeboten (siehe unten) |

**Sicherheits-Hotfix 2.11.12.1:** Diese Version korrigiert eine Sicherheitslücke in der lokalen Schnittstelle (Bridge) `http://127.0.0.1:17474` des MiniClients. Die Bridge beantwortet Anfragen aus dem Browser jetzt nur noch, wenn sie vom MSFS-Panel, von den eigenen Seiten des Clients oder von lokalen Programmen (z. B. dem Updater) kommen; Anfragen fremder Websites werden abgewiesen, und `GET /status` gibt das Discord-Anmelde-Token nicht mehr über HTTP heraus (die Desktop-Oberfläche erhält es weiterhin im Programm selbst). Geprüft werden Herkunft (`Origin`/`Sec-Fetch-Site`) und Zieladresse (`Host`, gegen DNS-Rebinding). **Geändert ist nur die Bridge**; am Flugablauf und an der übrigen Logik gegenüber der zuvor ausgelieferten 2.11.12 ist nichts geändert (Bytecode-Vergleich: nur `flyconomy_client.py` unterscheidet sich, und dort nur die Bridge-Funktionen). Die Versionsnummer 2.11.12.1 behandeln Client, Updater und Website wie „nicht älter als 2.11.12 und älter als 2.11.13“, damit das Update normal (nicht erzwungen) verteilt werden kann.

**Wie geprüft wurde:** Jede Python-Datei wurde mit CPython 3.14.0 übersetzt (dieselbe Version, die in den EXE-Dateien steckt) und mit den Modulen in den gebauten EXE-Dateien verglichen (Bytecode, Konstanten, Namen und Zeilentabellen). Die EXE des MiniClients 2.11.12.1 enthält genau diesen Quellstand; gegenüber der zuvor ausgelieferten 2.11.12 unterscheidet sich nur `flyconomy_client.py`. Die mitgelieferten Dateien (`ui/`, Icons, Logos) und die Versionsangaben (`file_version_info*.txt`) sind gleich, bis auf die Zeilenenden: Einige Textdateien unter `ui/` stecken in den EXE-Dateien mit Windows-Zeilenenden (CRLF), im Repository stehen alle Textdateien mit LF. Ein Klon unter Windows kann sie je nach Git-Einstellung (`core.autocrlf`) mit CRLF auschecken. Den Inhalt der Inno-Setup-Dateien selbst haben wir nicht entpackt.

**Lizenzdateien in den Setups:** Die Setups dieser Version (MiniClient 2.11.12.1 und das neu verpackte Updater-Setup 1.0.5) installieren `LICENSE.txt`, `THIRD-PARTY-NOTICES.md`, den Ordner `licenses/` und eine Verknüpfung `Quellcode.url` zu genau diesem Stand (Tag `v2.11.12.1`) in den Programmordner; derselbe Link steht im Willkommenstext der Setups. Lizenz, Fremdkomponenten und deren Lizenztexte stehen zusätzlich hier: [`LICENSE`](LICENSE), [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), [`licenses/`](licenses/).

**Ehrlicher Hinweis zu zwei Abweichungen:**

- Die Funktion `stabilize_load` in `flyconomy_client.py` steht so, wie sie im ausgelieferten MiniClient 2.11.12 steckt (übernommen aus dem älteren Quellstand und durch den Vergleich bestätigt). Sie wurde nach dem Build der 2.11.12 im internen Arbeitsstand noch einmal geändert, ohne die Versionsnummer zu erhöhen; diese Änderung wurde nie ausgeliefert und ist auch im Hotfix nicht enthalten. Der Branch `main` enthält sie.
- Browser inApp 1.0.1 wurde eine Woche vor dem Updater gebaut. Der einzige Unterschied zu `flyconomy_webapp.py` in diesem Stand: In `ensure_desktop_shortcut` fehlten damals die sechs Zeilen 180–185 (Rückgriff auf Microsoft Edge, wenn der Standardbrowser kein Chromium-Browser ist). Ohne diese Zeilen ist der Bytecode gleich. Die Fassung mit diesen Zeilen steckt im Updater 1.0.5, der `flyconomy_webapp.py` mitbringt.

**Voller Client 2.11.4:** Der volle Client wird nicht mehr weiterentwickelt und nicht mehr angeboten. Sein genauer Quellstand vom 19.08.2026 wurde nicht aufbewahrt und lässt sich nicht wiederherstellen. Sein Code steckt in `flyconomy_client.py` (Start ohne `--mini`).

### Bauen

Voraussetzungen: Windows 10/11 (64 bit), Python 3.14 (64 bit; gebaut mit CPython 3.14.0), für die Setups zusätzlich [Inno Setup 6](https://jrsoftware.org/isinfo.php) (verwendet: 6.7.3 für die Setups dieses Tags; die Programme der 2.11.12 wurden mit 6.7.0 verpackt).

```bat
git clone https://github.com/Struppelstrumpf/flclient.git
cd flclient
git checkout v2.11.12.1
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt pyinstaller -c build-constraints.txt

build_miniclient.bat   & rem dist\FlyConomyMiniClient.exe  + installer\FlyConomyMiniClient_Setup.exe
build_updater.bat      & rem dist\FlyConomyUpdater.exe     + installer\FlyConomyUpdater_Setup.exe
build_webapp.bat       & rem dist\FlyConomyWebApp.exe      (siehe unten)
build_exe.bat          & rem dist\FlyConomyClient.exe      (voller Client, eingestellt)
```

- Jede `build_*.bat` installiert die nötigen Pakete (ohne `-c build-constraints.txt`; wer vorher wie oben installiert, bekommt die geprüften Versionen) und ruft `python -m PyInstaller --noconfirm --clean <name>.spec` auf. Danach baut sie mit `ISCC.exe` das Setup (`*.iss`), wenn Inno Setup unter `Program Files` gefunden wird. Die Specs holen `SimConnect.dll` aus dem installierten Paket `SimConnect`.
- **Browser inApp:** Ausgeliefert wird das EXE selbst. Die Datei `FlyConomyWebApp_Setup.exe` auf flyconomy.de ist `dist\FlyConomyWebApp.exe` unter anderem Namen, kein Inno-Setup.
- **Voller Client:** `build_exe.bat` baut ihn aus diesem Stand. EXE und Setup tragen dabei weiter die Versionsangabe 2.11.4 (`file_version_info_client.txt`, `FlyConomyClient_Setup.iss`), obwohl der Code neuer ist.
- **Genaue Nachbildung:** In der Build-Umgebung lagen zusätzlich numpy, cryptography, bcrypt und psutil; PyInstaller hat sie mit eingepackt. Wer die EXE-Dateien genau nachbilden will, installiert sie zusätzlich: `python -m pip install numpy cryptography bcrypt psutil -c build-constraints.txt`.
- **`ui/phone/`** ist eine Kopie aus der nicht veröffentlichten Panel-Quelle. Änderungen bitte direkt in `ui/phone/` machen. `_build_phone_html.py` ist veraltet: Es liest die Panel-Quelle aus `../msfs-addon` (nicht in diesem Repository) und erzeugt nicht mehr genau die enthaltene `ui/phone/index.html`.

Ohne Build starten: `python flyconomy_miniclient.py` (MiniClient) oder `start_client.bat` (voller Client).

### Lizenz

Copyright (C) 2026 FlyConomy

Dieses Programm ist freie Software unter der **GNU Affero General Public License, Version 3** oder (nach Ihrer Wahl) jeder späteren Version (SPDX: `AGPL-3.0-or-later`). Den vollständigen Text enthält [`LICENSE`](LICENSE). Das Programm wird ohne jede Gewährleistung bereitgestellt. Das gilt für den Code und für die Grafikdateien in diesem Repository; Namens- und Markenrechte bleiben vorbehalten (siehe „Name und Logo“).

Grund für die AGPL: MiniClient und voller Client nutzen die Bibliothek [Python-SimConnect](https://github.com/odwdinc/Python-SimConnect), die unter der AGPL-3.0 steht. Alle weiteren Bibliotheken und ihre Lizenzen stehen in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), die Texte in [`licenses/`](licenses/).

**Teile, die nicht unter der AGPL stehen:** Die ausgelieferten Programme enthalten auch Code, der nicht unter der AGPL steht, vor allem `SimConnect.dll` von Microsoft (Microsoft Flight Simulator SDK). Diese Datei ist proprietäre Software von Microsoft. Sie kommt unverändert aus dem PyPI-Paket SimConnect 0.4.26 und liegt nicht in diesem Repository. Dafür gilt die folgende zusätzliche Erlaubnis.

**Zusätzliche Erlaubnis nach Abschnitt 7 der AGPL** (gilt für den Code von FlyConomy; maßgeblich ist der englische Text):

> If you modify this Program, or any covered work, by linking or combining it with Microsoft SimConnect.dll (part of the Microsoft Flight Simulator SDK), or a modified version of that library, the licensors of this Program grant you additional permission to convey the resulting work. Corresponding Source for such a combination need not include SimConnect.dll.

Auf Deutsch: Wer dieses Programm mit Microsofts `SimConnect.dll` verbindet und weitergibt, darf das; die DLL selbst muss dabei nicht als Quellcode mitgeliefert werden. Python-SimConnect bleibt unverändert unter seiner eigenen Lizenz.

### Name und Logo

„FlyConomy“, das FlyConomy-Logo und die Programm-Icons sind Kennzeichen von FlyConomy. Die AGPL gibt keine Rechte an Namen und Marken (AGPL Abschnitt 7 e). Die Grafikdateien liegen hier, damit sich die Programme so bauen lassen, wie sie ausgeliefert werden; urheberrechtlich stehen sie wie der Code unter der AGPL. Wer eine veränderte Fassung weitergibt, verwendet bitte einen eigenen Namen und ein eigenes Logo und gibt sie nicht als offizielles FlyConomy-Programm aus. Die Nutzung des FlyConomy-Dienstes (Server und Website) ist nicht Teil dieser Lizenz; dafür gelten die Regeln auf flyconomy.de.

### Fehler und Sicherheitslücken melden

- **Fehler im Client:** bitte als [Issue](https://github.com/Struppelstrumpf/flclient/issues) in diesem Repository. Bitte keine Tokens, Zugangsdaten oder Logs mit persönlichen Daten anhängen.
- **Sicherheitslücken:** bitte **nicht** öffentlich als Issue, sondern privat über GitHub melden: Reiter „Security“ → „Report a vulnerability“.
- **Probleme mit Konto oder Spiel:** über flyconomy.de bzw. die Problemmeldung im Client.

---

## English

Source code of the Windows programs of [FlyConomy](https://flyconomy.de), an economy simulation for Microsoft Flight Simulator (MSFS 2020/2024).

| Program | File | Purpose |
|---|---|---|
| **MiniClient** (main program) | `flyconomy_miniclient.py` → `flyconomy_client.py` | Runs in the system tray. Reads the simulator via SimConnect, starts and ends flights at FlyConomy, provides the local interface `http://127.0.0.1:17474` for the in-game panel, and connects to the website. |
| **Full client** (discontinued) | `flyconomy_client.py` (without `--mini`) | same code as the MiniClient, plus a window (pywebview) that shows flyconomy.de |
| **Updater** | `flyconomy_updater.py` | installs and updates the MiniClient and the MSFS panel, creates the "FlyConomy" desktop shortcut |
| **Browser inApp** (WebApp) | `flyconomy_webapp.py` | opens flyconomy.de in the default browser as an app window |

`ui/phone/` is a copy of the panel user interface; the MiniClient shows it at `http://127.0.0.1:17474/phone/` as a panel view in its own window on the PC (for example on a second screen).

### Not included

The **FlyConomy server, website, database and operations** are **not** part of this repository and are not published. The programs talk to the service at `https://flyconomy.de/api`, which requires a FlyConomy account (Discord login). The MSFS in-game panel is a separate download; only a copy of its user interface is included in `ui/phone/`.

### Versions

This state (tag `v2.11.12.1`) is the source code of the programs distributed on flyconomy.de as a security hotfix (successor of 2.11.12). The version currently offered is shown on [flyconomy.de/downloads](https://flyconomy.de/downloads). The development state and later versions are on branch `main` and in later tags. Always use the current version; older versions receive no bug or security fixes.

| Program | Version | Build | Source |
|---|---|---|---|
| MiniClient | 2.11.12.1 | 2026-10-16 | this state, verified (see below) |
| Updater | 1.0.5 | 2026-08-30, setup repackaged 2026-10-16 | this state, verified |
| Browser inApp | 1.0.1 | 2026-08-22 | this state except six lines, verified (see below) |
| panel copy `ui/phone` | 1.4.30 | – | this state |
| Full client | 2.11.4 | 2026-08-19 | **not kept**, no longer offered (see below) |

**Security hotfix 2.11.12.1:** this version fixes a security gap in the MiniClient's local interface (bridge) `http://127.0.0.1:17474`. The bridge now answers browser requests only from the MSFS panel, the client's own pages and local programs (such as the updater); requests from other websites are refused, and `GET /status` no longer returns the Discord login token over HTTP (the desktop UI still receives it in-process). Origin (`Origin`/`Sec-Fetch-Site`) and target (`Host`, against DNS rebinding) are checked. **Only the bridge changed**; the flight flow and the rest of the logic are unchanged compared to the previously distributed 2.11.12 (bytecode comparison: only `flyconomy_client.py` differs, and there only the bridge functions). The client, updater and website treat the version number 2.11.12.1 as "not older than 2.11.12 and older than 2.11.13", so the update can be rolled out as a normal (not forced) update.

**How this was verified:** every Python file was compiled with CPython 3.14.0 (the version inside the executables) and compared with the modules inside the built executables (bytecode, constants, names and line tables). The MiniClient 2.11.12.1 executable contains exactly this source; compared with the previously distributed 2.11.12 only `flyconomy_client.py` differs. The bundled files (`ui/`, icons, logos) and version resources match, except for line endings: some text files under `ui/` are stored with Windows line endings (CRLF) inside the executables, while the repository stores all text files with LF; a Windows clone may check them out with CRLF depending on `core.autocrlf`. The Inno Setup packages themselves were not unpacked.

**License files in the setups:** the setups of this version (MiniClient 2.11.12.1 and the repackaged Updater 1.0.5 setup) install `LICENSE.txt`, `THIRD-PARTY-NOTICES.md`, the `licenses/` folder and a `Quellcode.url` shortcut to exactly this state (tag `v2.11.12.1`) into the program folder; the setups' welcome text shows the same link. The license, third-party components and their texts are also here: [`LICENSE`](LICENSE), [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), [`licenses/`](licenses/).

**Honest note on two differences:**

- The function `stabilize_load` in `flyconomy_client.py` is as it is in the distributed MiniClient 2.11.12 (taken from the older source and confirmed by the comparison). After the 2.11.12 build it was changed again in the internal working copy without raising the version number; that change was never distributed and is not in the hotfix either. Branch `main` contains it.
- Browser inApp 1.0.1 was built a week before the Updater. The only difference to `flyconomy_webapp.py` in this state: `ensure_desktop_shortcut` did not yet have lines 180–185 (fallback to Microsoft Edge when the default browser is not Chromium-based). Without those lines the bytecode matches. The version with these lines is the one bundled in Updater 1.0.5.

**Full client 2.11.4:** discontinued and no longer offered. Its exact source from 2026-08-19 was not kept and cannot be restored. Its code lives in `flyconomy_client.py` (run without `--mini`).

### Building

Requirements: Windows 10/11 (64 bit), Python 3.14 (64 bit; built with CPython 3.14.0), and [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the setups (used: 6.7.3 for this tag's setups; the 2.11.12 programs were packaged with 6.7.0). Create a virtual environment, run `python -m pip install -r requirements.txt pyinstaller -c build-constraints.txt`, then run `build_miniclient.bat`, `build_updater.bat`, `build_webapp.bat` or `build_exe.bat` (see the commands in the German section above). Each script installs its packages (without the constraints file, so install as shown first), runs PyInstaller with the matching `.spec` file and, if Inno Setup is installed, `ISCC.exe` with the matching `.iss` file. Run from source with `python flyconomy_miniclient.py`.

- **Browser inApp** is distributed as the bare executable: `FlyConomyWebApp_Setup.exe` on flyconomy.de is `dist\FlyConomyWebApp.exe` under another name, not an Inno setup.
- **Full client:** `build_exe.bat` builds it from this state; the executable and setup still carry the version label 2.11.4 (`file_version_info_client.txt`, `FlyConomyClient_Setup.iss`) although the code is newer.
- **Exact rebuild:** the build environment also contained numpy, cryptography, bcrypt and psutil, which PyInstaller bundled. To reproduce the executables exactly, also run `python -m pip install numpy cryptography bcrypt psutil -c build-constraints.txt`.
- **`ui/phone/`** is copied from the unpublished panel source; edit the files in `ui/phone/` directly. `_build_phone_html.py` is outdated: it reads the panel source from `../msfs-addon` (not in this repository) and no longer produces exactly the included `ui/phone/index.html`.

### License

Copyright (C) 2026 FlyConomy

This program is free software under the **GNU Affero General Public License, version 3** or (at your option) any later version (SPDX: `AGPL-3.0-or-later`); see [`LICENSE`](LICENSE). It comes without any warranty. This applies to the code and to the image files in this repository; name and trademark rights are reserved (see "Name and logo"). The reason for the AGPL is the [Python-SimConnect](https://github.com/odwdinc/Python-SimConnect) library (AGPL-3.0) used by the MiniClient and the full client. All other components are listed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md), their texts are in [`licenses/`](licenses/).

**Parts not under the AGPL:** the distributed programs also contain code that is not under the AGPL, above all Microsoft's `SimConnect.dll` (Microsoft Flight Simulator SDK). This file is proprietary Microsoft software. It is shipped unmodified as contained in the PyPI package SimConnect 0.4.26 and is not part of this repository. The following additional permission covers it.

**Additional permission under AGPL section 7** (applies to FlyConomy's code):

> If you modify this Program, or any covered work, by linking or combining it with Microsoft SimConnect.dll (part of the Microsoft Flight Simulator SDK), or a modified version of that library, the licensors of this Program grant you additional permission to convey the resulting work. Corresponding Source for such a combination need not include SimConnect.dll.

Python-SimConnect itself remains under its own license.

### Name and logo

"FlyConomy", the FlyConomy logo and the program icons are brand marks of FlyConomy. The AGPL does not grant trademark rights (AGPL section 7 e). The image files are included so the programs can be built as distributed; for copyright they are under the AGPL like the code. If you distribute a modified version, please use your own name and logo and do not present it as an official FlyConomy program. Using the FlyConomy service (server and website) is not covered by this license; the rules on flyconomy.de apply.

### Reporting bugs and security issues

- **Client bugs:** open an [issue](https://github.com/Struppelstrumpf/flclient/issues). Please do not attach tokens, credentials or logs with personal data.
- **Security issues:** please do **not** open a public issue; report privately via GitHub: "Security" tab → "Report a vulnerability".
- **Account or game problems:** via flyconomy.de or the problem report in the client.
