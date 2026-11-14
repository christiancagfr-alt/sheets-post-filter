#ifndef MyAppVersion
  #define MyAppVersion "1.5.19"
#endif

#define MyAppName "数据汇总工具"
#define MyAppExeName "数据汇总工具.exe"
#ifndef MyAppSource
  #define MyAppSource "release\data-summary-tool"
#endif

[Setup]
AppId={{9D47EB5B-1B34-4AE5-88D3-C3B20D8AC104}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=数据汇总工具
DefaultDirName={localappdata}\sheets-post-filter
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=data-summary-tool-setup-v{#MyAppVersion}
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\logo.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "chinesesimp"; MessagesFile: "installer-ChineseSimplified.isl"

[Files]
Source: "{#MyAppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "web\static\logo.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "web\static\logo.png"; DestDir: "{app}\web\static"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\logo.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动{#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
