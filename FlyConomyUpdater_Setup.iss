; FlyConomy Updater – Installer (Inno Setup 6)
#define MyAppName "FlyConomy Updater"
#define MyAppVersion "1.0.6"
#define MyAppPublisher "FlyConomy"
#define MyAppURL "https://flyconomy.de"
; Source of exactly this setup (AGPL 6 d): the tag of the client release it ships with (update at release)
#define MySourceURL "https://github.com/Struppelstrumpf/flclient/tree/v2.11.13"
#define MyAppExeName "FlyConomyUpdater.exe"

[Setup]
AppId={{B7E4D2A1-9C31-4F80-8A12-7C3E9F1A2B10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/downloads
DefaultDirName={localappdata}\FlyConomyUpdater
DefaultGroupName=FlyConomy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FlyConomyUpdater_Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
CloseApplications=yes
RestartApplications=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FlyConomy Updater
VersionInfoProductName={#MyAppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknuepfung erstellen"; GroupDescription: "Zusaetzliche Symbole:"; Flags: unchecked
Name: "startup"; Description: "Beim Windows-Start automatisch starten"; GroupDescription: "Autostart:"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "tray_icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
; Link to the source code (GNU AGPL-3.0) in the program folder
Filename: "{app}\Quellcode.url"; Section: "InternetShortcut"; Key: "URL"; String: "{#MySourceURL}"

[UninstallDelete]
Type: files; Name: "{app}\Quellcode.url"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"
Name: "{group}\FlyConomy Updater deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tray"; IconFilename: "{app}\logo.ico"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Updater starten"; Flags: nowait postinstall skipifsilent

[Messages]
german.WelcomeLabel2=Dieser Assistent installiert [name/ver].%n%nDanach erkennt der Updater MSFS und den Community-Ordner, installiert MiniClient und Panel, legt FlyConomy Browser inApp auf den Desktop und haelt alles aktuell.%n%nQuellcode und Lizenz (GNU AGPL-3.0): {#MySourceURL}
english.WelcomeLabel2=This installs [name/ver].%n%nThe updater then finds MSFS and the Community folder, installs MiniClient and the in-sim panel, puts FlyConomy Browser inApp on the desktop, and keeps everything current.%n%nSource code and license (GNU AGPL-3.0): {#MySourceURL}
