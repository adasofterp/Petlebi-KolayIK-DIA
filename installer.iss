; Inno Setup Kurulum Sihirbazı Betiği (Petlebi-KolayIK-DIA-Setup.exe)
; Ada-Soft Dijital Dönüşüm Merkezi — https://www.adasoftddm.com
[Setup]
AppId={{8F9D4A2E-5C31-4B8E-9A1D-7E6B2C3D4F5A}
AppName=Petlebi Kolay İK - DİA ERP Entegrasyon (Ada-Soft DDM)
AppVersion=1.1.0
AppPublisher=Ada-Soft Dijital Dönüşüm Merkezi (adasoftddm.com)
AppPublisherURL=https://www.adasoftddm.com
AppSupportURL=https://www.adasoftddm.com
AppUpdatesURL=https://www.adasoftddm.com
DefaultDirName={autopf}\Petlebi-KolayIK-DIA
DefaultGroupName=Ada-Soft DDM - Petlebi Kolay İK & DİA ERP
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=Petlebi-KolayIK-DIA-Setup
SetupIconFile=src\static\adasoftlogo.ico
UninstallDisplayIcon={app}\Petlebi-KolayIK-DIA.exe
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Petlebi-KolayIK-DIA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "src\*"; DestDir: "{app}\src"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "version.json"; DestDir: "{app}"; Flags: ignoreversion
; Güvenlik Kuralı: Yerel .env dosyası ASLA kurulum paketine gömülmez; yalnız .env.example kopyalanır
Source: ".env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\Petlebi Kolay İK - DİA ERP (Ada-Soft DDM)"; Filename: "{app}\Petlebi-KolayIK-DIA.exe"; IconFilename: "{app}\src\static\adasoftlogo.ico"
Name: "{autodesktop}\Petlebi Kolay İK - DİA ERP"; Filename: "{app}\Petlebi-KolayIK-DIA.exe"; IconFilename: "{app}\src\static\adasoftlogo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Petlebi-KolayIK-DIA.exe"; Description: "{cm:LaunchProgram,Petlebi Kolay İK - DİA ERP}"; Flags: nowait postinstall skipifsilent
