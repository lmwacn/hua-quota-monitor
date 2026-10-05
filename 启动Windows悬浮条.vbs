Option Explicit
Dim shell, fs, root, pythonw
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fs.FileExists(pythonw) Then
    MsgBox "Please install the Windows dependencies first (see README).", 48, "Codex quota"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & fs.BuildPath(root, "main.py") & Chr(34) & " widget", 0, False
