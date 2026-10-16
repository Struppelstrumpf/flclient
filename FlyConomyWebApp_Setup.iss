; FlyConomy Browser inApp – Desktop-Installer (Inno Setup 6)
#define MyAppName "FlyConomy"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "FlyConomy"
#define MyAppURL "https://flyconomy.de"
#define MyAppExeName "FlyConomyWebApp.exe"

[Setup]
AppId={{C8F5E3B2-0D42-5A91-9B23-8D4F0A2C3C21}
AppName={#MyAppName} Browser inApp
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/downloads
DefaultDirName={localappdata}\FlyConomyWebApp
DefaultGroupName=FlyConomy
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=FlyConomyWebApp_Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FlyConomy Browser inApp
VersionInfoProductName={#MyAppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "FlyConomy oeffnen"; Flags: nowait postinstall skipifsilent

[Messages]
german.WelcomeLabel2=Dieser Assistent installiert FlyConomy als Desktop-App.%n%nEine Verknuepfung liegt danach auf dem Desktop. Sie oeffnet FlyConomy im Standardbrowser – bei Chrome, Edge, Brave und Co. ohne Adressleiste.
english.WelcomeLabel2=This installs FlyConomy as a desktop app.%n%nA shortcut is placed on the desktop. It opens FlyConomy in your default browser – Chrome, Edge, Brave and similar without an address bar.
