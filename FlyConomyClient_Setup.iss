; FlyConomy Client – Installer (Inno Setup 6)
#define MyAppName "FlyConomy"
#define MyAppVersion "2.11.4"
#define MyAppPublisher "FlyConomy"
#define MyAppURL "https://flyconomy.de"
#define MySourceURL "https://github.com/Struppelstrumpf/flclient"
#define MyAppExeName "FlyConomyClient.exe"

[Setup]
AppId={{8F3C2A91-6E4B-4D2A-9C11-FLYCONOMYCLI2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\FlyConomy
DefaultGroupName=FlyConomy
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FlyConomyClient_Setup
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
VersionInfoDescription=FlyConomy Desktop Client
VersionInfoProductName={#MyAppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Zusätzliche Symbole:"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion
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
Name: "{group}\FlyConomy deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "FlyConomy starten und mit Discord anmelden"; Flags: nowait postinstall skipifsilent

[Messages]
german.WelcomeLabel2=Dieser Assistent installiert [name/ver] auf Ihrem Computer.%n%nNach der Installation melden Sie sich einmal mit Discord an. Ohne Login ist die App nicht nutzbar – es gibt keinen Gastmodus.%n%nQuellcode und Lizenz (GNU AGPL-3.0): {#MySourceURL}
english.WelcomeLabel2=This will install [name/ver] on your computer.%n%nAfter installation, sign in once with Discord. Without login the app cannot be used – there is no guest mode.%n%nSource code and license (GNU AGPL-3.0): {#MySourceURL}

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
end;
