; FlyConomy MiniClient – Installer (Inno Setup 6)
#define MyAppName "FlyConomy MiniClient"
#define MyAppVersion "2.11.12.1"
#define MyAppPublisher "FlyConomy"
#define MyAppURL "https://flyconomy.de"
; Source of exactly this version (AGPL 6 d): the release tag v<version> of the public repo
#define MySourceURL "https://github.com/Struppelstrumpf/flclient/tree/v" + MyAppVersion
#define MyAppExeName "FlyConomyMiniClient.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-FLYCONOMYMINI}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/help
DefaultDirName={localappdata}\FlyConomyMiniClient
DefaultGroupName=FlyConomy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FlyConomyMiniClient_Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FlyConomy MiniClient Bridge
VersionInfoProductName={#MyAppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknuepfung erstellen"; GroupDescription: "Zusaetzliche Symbole:"; Flags: unchecked
Name: "startup"; Description: "Beim Windows-Start automatisch starten"; GroupDescription: "Autostart:"; Flags: unchecked

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
Name: "{group}\FlyConomy MiniClient deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "MiniClient starten und mit Discord anmelden"; Flags: nowait postinstall skipifsilent

[Messages]
german.WelcomeLabel2=Dieser Assistent installiert [name/ver].%n%nDer MiniClient laeuft danach im System-Tray und verbindet Website, MSFS und Panel.%n%nBei Windows-SmartScreen: Weitere Informationen -> Trotzdem ausfuehren.%n%nQuellcode und Lizenz (GNU AGPL-3.0): {#MySourceURL}
english.WelcomeLabel2=This installs [name/ver].%n%nThe MiniClient then runs in the system tray and bridges website, MSFS and panel.%n%nIf Windows SmartScreen appears: More info -> Run anyway.%n%nSource code and license (GNU AGPL-3.0): {#MySourceURL}
