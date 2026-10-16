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

Der Branch `main` ist der Entwicklungsstand. Jede ausgelieferte Version bekommt einen Tag `v<Version>`; nur getaggte Stände entsprechen ausgelieferten Programmen. Welche Version gerade angeboten wird, steht auf [flyconomy.de/downloads](https://flyconomy.de/downloads). Bitte immer die aktuelle Version verwenden; ältere Versionen bekommen keine Fehler- und Sicherheitskorrekturen.

#### Tag `v2.11.13` und Branch `main` – ausgelieferter Stand (Pflicht-Update für alle)

| Programm | Version | Build | Hinweis |
|---|---|---|---|
| MiniClient | 2.11.13 | 16.10.2026 | Pflicht-Update: ältere MiniClients starten danach keine Flüge mehr |
| Updater | 1.0.7 | 16.10.2026 | prüft jeden Download gegen die SHA-256 auf flyconomy.de; installiert den vollen Client nicht mehr |
| Browser inApp | 1.0.1 | 22.08.2026 | unverändert gegenüber `v2.11.12.1` |
| Panel-Kopie `ui/phone` | 1.4.31 | – | |
| Voller Client | – | – | eingestellt, wird nicht mehr angeboten |

Die Setups von MiniClient und Updater installieren `LICENSE.txt`, `THIRD-PARTY-NOTICES.md`, den Ordner `licenses/` und eine Verknüpfung `Quellcode.url` zu diesem Tag (`v2.11.13`) in den Programmordner. Gebaut mit CPython 3.14.0, PyInstaller 6.17.0 und Inno Setup 6.7.3 (Pakete wie `build-constraints.txt`); der Bytecode der EXE-Dateien entspricht diesem Quellstand (Vergleich wie unten).

Neu gegenüber `v2.11.12.1` (Auswahl): Flüge werden nach einem Absturz oder Neustart von MSFS oder des MiniClients wiederhergestellt („Flug fortsetzen?“ mit „Zur letzten Position“), kein lokaler Teleport-Abbruch mehr (der Server entscheidet), ein gespeichertes Flugende geht nie verloren (auch nicht bei „Abbrechen“ ohne Serververbindung), Anmeldung wird regelmäßig verlängert, Vergleichscode beim Koppeln mit der Website, Website-Befehle laufen in einem eigenen Thread, die Verbindung zur Website verbindet sich nach einer Ablehnung nicht mehr in einer Schleife neu, ungültige Höhenwerte werden als „unbekannt“ gesendet.

#### Tag `v2.11.12.1` – früher ausgelieferter Stand (Sicherheits-Hotfix)

| Programm | Version | Build | Quellstand |
|---|---|---|---|
| MiniClient | 2.11.12.1 | 16.10.2026 | `v2.11.12.1`, geprüft (siehe unten) |
| Updater | 1.0.5 | 30.08.2026, Setup neu verpackt 16.10.2026 | `v2.11.12.1`, geprüft |
| Browser inApp | 1.0.1 | 22.08.2026 | `v2.11.12.1` bis auf sechs Zeilen, geprüft (siehe unten) |
| Panel-Kopie `ui/phone` | 1.4.30 | – | `v2.11.12.1` |
| Voller Client | 2.11.4 | 19.08.2026 | **nicht erhalten**, wird nicht mehr angeboten (siehe unten) |

`v2.11.12.1` ist ein Sicherheits-Hotfix auf dem Quellstand der ab 30.08.2026 ausgelieferten 2.11.12: Die lokale Bridge `http://127.0.0.1:17474` beantwortet Browser-Anfragen nur noch vom MSFS-Panel, von den eigenen Seiten des Clients und von lokalen Programmen; fremde Websites werden abgewiesen, und `GET /status` gibt das Anmelde-Token nicht mehr über HTTP heraus. Nur die Bridge ist geändert, der Flugablauf nicht (Bytecode-Vergleich). Die zuvor ausgelieferte 2.11.12 wird nicht mehr angeboten.

**Wie geprüft wurde:** Jede Python-Datei wurde mit CPython 3.14.0 übersetzt (dieselbe Version, die in den EXE-Dateien steckt) und mit den Modulen in den gebauten EXE-Dateien verglichen (Bytecode, Konstanten, Namen und Zeilentabellen). Die EXE des MiniClients 2.11.12.1 enthält genau den Quellstand von `v2.11.12.1`; gegenüber der zuvor ausgelieferten 2.11.12 unterscheidet sich nur `flyconomy_client.py` (und dort nur die Bridge). Die mitgelieferten Dateien (`ui/`, Icons, Logos) und die Versionsangaben (`file_version_info*.txt`) sind gleich, bis auf die Zeilenenden: Einige Textdateien unter `ui/` stecken in den EXE-Dateien mit Windows-Zeilenenden (CRLF), im Repository stehen alle Textdateien mit LF. Ein Klon unter Windows kann sie je nach Git-Einstellung (`core.autocrlf`) mit CRLF auschecken. Den Inhalt der Inno-Setup-Dateien selbst haben wir nicht entpackt. Die Setups von `v2.11.12.1` installieren Lizenz, Fremdkomponenten und deren Texte mit (wie die Setups aus `main`).

**Ehrlicher Hinweis zu zwei Abweichungen:**

- Nach dem Build von MiniClient 2.11.12 wurde im internen Arbeitsstand die Funktion `stabilize_load` in `flyconomy_client.py` geändert, ohne die Versionsnummer zu erhöhen. Diese Änderung wurde nie ausgeliefert. Im Tag `v2.11.12.1` steht die Funktion deshalb so, wie sie im ausgelieferten MiniClient 2.11.12 steckt (übernommen aus dem älteren Quellstand und durch den Vergleich bestätigt). Der Stand `main` enthält die Änderung.
- Browser inApp 1.0.1 wurde eine Woche vor dem Updater gebaut. Der einzige Unterschied zu `flyconomy_webapp.py` im Tag `v2.11.12.1`: In `ensure_desktop_shortcut` fehlten damals die sechs Zeilen 180–185 (Rückgriff auf Microsoft Edge, wenn der Standardbrowser kein Chromium-Browser ist). Ohne diese Zeilen ist der Bytecode gleich. Die Fassung mit diesen Zeilen steckt im Updater 1.0.5, der `flyconomy_webapp.py` mitbringt.

**Voller Client 2.11.4:** Der volle Client wird nicht mehr weiterentwickelt und nicht mehr angeboten. Sein genauer Quellstand vom 19.08.2026 wurde nicht aufbewahrt und lässt sich nicht wiederherstellen. Sein Code steckt in `flyconomy_client.py` (Start ohne `--mini`).

### Bauen

Voraussetzungen: Windows 10/11 (64 bit), Python 3.14 (64 bit; die ausgelieferten Programme wurden mit CPython 3.14.0 gebaut), für die Setups zusätzlich [Inno Setup 6](https://jrsoftware.org/isinfo.php) (verwendet: 6.7.0 für 2.11.12, 6.7.3 für die Setups 2.11.12.1).

```bat
git clone https://github.com/Struppelstrumpf/flclient.git
cd flclient
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt pyinstaller -c build-constraints.txt

build_miniclient.bat   & rem dist\FlyConomyMiniClient.exe  + installer\FlyConomyMiniClient_Setup.exe
build_updater.bat      & rem dist\FlyConomyUpdater.exe     + installer\FlyConomyUpdater_Setup.exe
build_webapp.bat       & rem dist\FlyConomyWebApp.exe      (siehe unten)
build_exe.bat          & rem dist\FlyConomyClient.exe      (voller Client, eingestellt)
```

- Jede `build_*.bat` installiert die nötigen Pakete (ohne `-c build-constraints.txt`; wer vorher wie oben installiert, bekommt die Versionen der ausgelieferten Builds) und ruft `python -m PyInstaller --noconfirm --clean <name>.spec` auf. Danach baut sie mit `ISCC.exe` das Setup (`*.iss`), wenn Inno Setup unter `Program Files` gefunden wird. Die Specs holen `SimConnect.dll` aus dem installierten Paket `SimConnect`.
- **Browser inApp:** Ausgeliefert wird das EXE selbst. Die Datei `FlyConomyWebApp_Setup.exe` auf flyconomy.de ist `dist\FlyConomyWebApp.exe` unter anderem Namen, kein Inno-Setup.
- **Voller Client:** `build_exe.bat` baut ihn aus dem jeweiligen Stand. EXE und Setup tragen dabei weiter die Versionsangabe 2.11.4 (`file_version_info_client.txt`, `FlyConomyClient_Setup.iss`), obwohl der Code neuer ist.
- **Ausgelieferter Stand:** `git checkout v2.11.13` holt den Quellstand der aktuell ausgelieferten Programme (MiniClient 2.11.13, Updater 1.0.7); `git checkout v2.11.12.1` den der Version davor.
- **Genaue Nachbildung:** In der Build-Umgebung der ausgelieferten Programme lagen zusätzlich numpy, cryptography, bcrypt und psutil; PyInstaller hat sie mit eingepackt. Wer die EXE-Dateien genau nachbilden will, installiert sie zusätzlich: `python -m pip install numpy cryptography bcrypt psutil -c build-constraints.txt`.
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

Branch `main` is the development state. Every distributed version gets a tag `v<version>`; only tagged states correspond to distributed programs. The version currently offered is shown on [flyconomy.de/downloads](https://flyconomy.de/downloads). Please always use the current version; older versions receive no bug or security fixes.

- **Tag `v2.11.13` and branch `main` – currently distributed (mandatory update for everyone):** MiniClient 2.11.13 (built 2026-10-16; older MiniClients can no longer start flights), Updater 1.0.7 (built 2026-10-16; checks every download against the SHA-256 published on flyconomy.de, no longer installs the full client), Browser inApp 1.0.1 (unchanged), panel copy 1.4.31; full client discontinued. The setups install `LICENSE.txt`, `THIRD-PARTY-NOTICES.md`, the `licenses/` folder and a `Quellcode.url` shortcut to tag `v2.11.13`. Built with CPython 3.14.0, PyInstaller 6.17.0 and Inno Setup 6.7.3; the bytecode of the executables matches this source. New since `v2.11.12.1` (selection): flights are restored after an MSFS or MiniClient crash/restart ("continue flight?" with "back to last position"), no local teleport abort any more (the server decides), a stored landing is never lost (not even on "cancel" without a server connection), the sign-in is renewed regularly, a comparison code when pairing with the website, website commands run in their own thread, the website connection no longer reconnects in a loop after a rejection, invalid altitude values are sent as unknown.
- **Tag `v2.11.12.1` – previously distributed (security hotfix):** MiniClient 2.11.12.1 (built 2026-10-16), Updater 1.0.5 (2026-08-30, setup repackaged 2026-10-16), Browser inApp 1.0.1 (2026-08-22), panel copy 1.4.30. These setups install the license files. `v2.11.12.1` is a security hotfix on top of the 2.11.12 source distributed from 2026-08-30: the local bridge `http://127.0.0.1:17474` now answers browser requests only from the MSFS panel, the client's own pages and local programs, refuses other websites, and no longer returns the login token over HTTP; only the bridge changed (bytecode comparison). The earlier 2.11.12 is no longer offered. Full client 2.11.4 (2026-08-19): source not kept, no longer offered (see below).

**How this was verified:** every Python file was compiled with CPython 3.14.0 (the version inside the executables) and compared with the modules inside the distributed executables (bytecode, constants, names and line tables). The MiniClient 2.11.12.1 executable contains exactly the `v2.11.12.1` source; compared with the previously distributed 2.11.12 only `flyconomy_client.py` differs (and there only the bridge). The bundled files (`ui/`, icons, logos) and the version resources match as well, except for line endings: some text files under `ui/` are stored with Windows line endings (CRLF) inside the executables, while the repository stores all text files with LF; a Windows clone may check them out with CRLF depending on `core.autocrlf`. The SHA-256 hashes of the setups on flyconomy.de match the build outputs. The Inno Setup packages themselves were not unpacked.

**Honest note on two differences:**

- After the MiniClient 2.11.12 build, the function `stabilize_load` in `flyconomy_client.py` was changed in the internal working copy without raising the version number. That change was never distributed, so tag `v2.11.12.1` contains the function as it is in the distributed MiniClient 2.11.12 (taken from the older source and confirmed by the comparison). Branch `main` contains the change.
- Browser inApp 1.0.1 was built a week before the Updater. The only difference to `flyconomy_webapp.py` in tag `v2.11.12.1`: `ensure_desktop_shortcut` did not yet have lines 180–185 (fallback to Microsoft Edge when the default browser is not Chromium-based). Without those lines the bytecode matches. The version with these lines is the one bundled in Updater 1.0.5.

**Full client 2.11.4:** discontinued and no longer offered. Its exact source from 2026-08-19 was not kept and cannot be restored. Its code lives in `flyconomy_client.py` (run without `--mini`).

### Building

Requirements: Windows 10/11 (64 bit), Python 3.14 (64 bit; the distributed programs were built with CPython 3.14.0), and [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the setups (used: 6.7.0 for 2.11.12, 6.7.3 for the 2.11.12.1 setups). Create a virtual environment, run `python -m pip install -r requirements.txt pyinstaller -c build-constraints.txt`, then run `build_miniclient.bat`, `build_updater.bat`, `build_webapp.bat` or `build_exe.bat` (see the commands in the German section above). Each script installs its packages (without the constraints file, so install as shown first), runs PyInstaller with the matching `.spec` file and, if Inno Setup is installed, `ISCC.exe` with the matching `.iss` file. Run from source with `python flyconomy_miniclient.py`.

- **Browser inApp** is distributed as the bare executable: `FlyConomyWebApp_Setup.exe` on flyconomy.de is `dist\FlyConomyWebApp.exe` under another name, not an Inno setup.
- **Full client:** `build_exe.bat` builds it from either state; the executable and setup still carry the version label 2.11.4 (`file_version_info_client.txt`, `FlyConomyClient_Setup.iss`) although the code is newer.
- **Distributed state:** `git checkout v2.11.13` gets the source of the programs currently distributed (MiniClient 2.11.13, Updater 1.0.7); `git checkout v2.11.12.1` the previous one.
- **Exact rebuild:** the build environment of the distributed programs also contained numpy, cryptography, bcrypt and psutil, which PyInstaller bundled. To reproduce the executables exactly, also run `python -m pip install numpy cryptography bcrypt psutil -c build-constraints.txt`.
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
