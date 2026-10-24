#ifndef AppVersion
  #define AppVersion "1.0.2"
#endif
#ifndef BuildRoot
  #define BuildRoot "..\.desktop-build"
#endif
[Setup]
AppId={{84A31EF9-33C0-492B-921B-AFD0809F68AE}
AppName=Собеседник
AppVersion={#AppVersion}
AppPublisher=Sobesednik contributors
AppPublisherURL=https://github.com/Roland525/sobesednik
AppSupportURL=https://github.com/Roland525/sobesednik/issues
DefaultDirName={localappdata}\Programs\Sobesednik
DefaultGroupName=Собеседник
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#BuildRoot}\installer
OutputBaseFilename=Sobesednik-Setup-{#AppVersion}
SetupIconFile={#BuildRoot}\icon.ico
UninstallDisplayIcon={app}\Sobesednik.exe
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce
[Files]
Source: "{#BuildRoot}\frozen\Sobesednik\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Собеседник"; Filename: "{app}\Sobesednik.exe"
Name: "{autodesktop}\Собеседник"; Filename: "{app}\Sobesednik.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\Sobesednik.exe"; Description: "Открыть Собеседника"; Flags: nowait postinstall skipifsilent
; Working data in LocalAppData\Sobesednik is intentionally not uninstalled.
