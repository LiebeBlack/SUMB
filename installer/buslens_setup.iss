; ---------------------------------------------------------------------------
; BusLens — Instalador Inno Setup (Windows 10/11, x64)
;
; Requiere Inno Setup 6.3+ (WizardStyle=modern y x64compatible).
; La versión se inyecta con build_exe.py vía installer/version.iss o con:
;     ISCC.exe buslens_setup.iss /DMyAppVersion=1.0.0
; ---------------------------------------------------------------------------

#ifndef MyAppVersion
  #include "version.iss"
#endif

#ifndef MyAppName
  #define MyAppName "BusLens"
#endif

#define MyAppPublisher "BusLens"
#define MyAppExeName "BusLens.exe"
#define MyAppDataDir "{userappdata}\BusLens"

[Setup]
; --- Identidad del producto -------------------------------------------------
AppId={{6E4F2C8A-9B31-4D7A-9C10-8A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/buslens
AppSupportURL=https://github.com/buslens
AppUpdatesURL=https://github.com/buslens
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Monitor e Inspector de Protocolos USB / Hardware
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (c) BusLens. MIT License.

; --- Arquitectura 64-bit ----------------------------------------------------
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; --- Directorio y privilegios ----------------------------------------------
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; --- Apariencia moderna de Windows 10/11 ------------------------------------
WizardStyle=modern
WizardResizable=yes
WizardSizePercent=110
SetupIconFile=..\assets\buslens.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no

; --- Licencia (opcional; descomentar si existe) ------------------------------
; LicenseFile=..\LICENSE

; --- Salida ---------------------------------------------------------------
OutputDir=..\dist
OutputBaseFilename=BusLens_Setup_v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
InternalCompressLevel=ultra64
MinVersion=10.0.19041

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Iniciar {#MyAppName} con Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Ejecutable autocontenido producido por build_exe.py
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\buslens.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
; Driver del Modo Avanzado (Beta) — solo si fue compilado con el WDK
Source: "..\kernel\buslens_filter.sys"; DestDir: "{app}\kernel"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\kernel\buslens_filter.inf"; DestDir: "{app}\kernel"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\buslens.ico"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\buslens.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\buslens.ico"; Tasks: desktopicon; WorkingDir: "{app}"

[Registry]
; App Paths para que «BusLens» se pueda lanzar desde Ejecutar/Shell.
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey

; Registro de versión instalada (consultable por scripts/inspección).
Root: HKLM; Subkey: "Software\BusLens"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\BusLens"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\BusLens"; ValueType: dword; ValueName: "Installed"; ValueData: 1; Flags: uninsdeletekey

[Run]
; Lanzar la app al finalizar (opcional, desmarcado por defecto).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Cero residuos: datos de usuario generados por la aplicación.
Type: filesandordirs; Name: "{userappdata}\BusLens"
Type: filesandordirs; Name: "{localappdata}\BusLens"

[Code]
// ---------------------------------------------------------------------------
// El driver del Modo Avanzado es opcional: solo se instala si fue
// compilado con el WDK y acompañó al instalador.
// ---------------------------------------------------------------------------
function KernelDriverExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{src}\..\kernel\buslens_filter.sys')) or
            FileExists(ExpandConstant('{app}\kernel\buslens_filter.sys'));
end;

// ---------------------------------------------------------------------------
// Limpieza defensiva: si el proceso de la app sigue vivo al desinstalar,
// se cierra antes de borrar el directorio de instalación.
// ---------------------------------------------------------------------------
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'),
      ExpandConstant(' /IM {#MyAppExeName} /F /T'),
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// Aviso si se intenta instalar sobre una versión más reciente: lee la clave
// de versión registrada por [Registry] y aborta con mensaje claro.
function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
begin
  Result := True;
  if RegQueryStringValue(HKLM, 'Software\BusLens', 'Version', InstalledVersion) then
  begin
    if CompareStr(InstalledVersion, '{#MyAppVersion}') > 0 then
    begin
      Result := MsgBox('Ya hay instalada una versión más reciente de BusLens ('
        + InstalledVersion + '). ¿Continuar con la instalación de {#MyAppVersion}?',
        mbConfirmation, MB_YESNO) = IDYES;
    end;
  end;
end;