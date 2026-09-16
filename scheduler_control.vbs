' ============================================================================
'  paper-notifier scheduler control
'
'  Runs the daily paper-notifier scheduler hidden in the background (no
'  console window) and manages autostart at Windows logon.
'
'  Double-click this file for an interactive menu, or pass an action:
'      wscript scheduler_control.vbs start
'      wscript scheduler_control.vbs stop
'      wscript scheduler_control.vbs status
'      wscript scheduler_control.vbs autostart-on
'      wscript scheduler_control.vbs autostart-off
' ============================================================================
Option Explicit

Const APP_TITLE = "paper-notifier scheduler"
Const LOG_REL   = "logs\scheduler.log"
Const LNK_NAME  = "paper-notifier scheduler.lnk"

Dim fso, shell, scriptDir, logPath, quoteChar
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
logPath = fso.BuildPath(scriptDir, LOG_REL)
quoteChar = Chr(34)

EnsureLogsFolder

If WScript.Arguments.Count > 0 Then
    RunAction LCase(Trim(WScript.Arguments(0))), False
Else
    Dim menuChoice
    Do
        menuChoice = PromptMenu()
        If menuChoice = "" Or menuChoice = "0" Then Exit Do
        Select Case menuChoice
            Case "1"
                RunAction "start", True
            Case "2"
                RunAction "stop", True
            Case "3"
                RunAction "status", True
            Case "4"
                RunAction "autostart-on", True
            Case "5"
                RunAction "autostart-off", True
            Case Else
                MsgBox "Please enter a number between 0 and 5.", vbExclamation, APP_TITLE
        End Select
    Loop
End If

Sub RunAction(action, interactive)
    Dim quiet, effective, message
    quiet = False
    effective = action
    If effective = "start-quiet" Then
        quiet = True
        effective = "start"
    End If

    Select Case effective
        Case "start"
            message = StartBackground()
        Case "stop"
            message = StopScheduler(interactive)
        Case "status"
            message = StatusText()
        Case "autostart-on"
            message = AutostartOn()
        Case "autostart-off"
            message = AutostartOff()
        Case Else
            message = "Unknown action: " & action & vbCrLf & vbCrLf & UsageText()
    End Select

    LogLine "action=" & effective & " | " & FirstLine(message)

    If quiet Then Exit Sub
    If interactive Then
        MsgBox message, vbInformation, APP_TITLE
    Else
        WScript.Echo message
    End If
End Sub

Function StartBackground()
    Dim pythonExe, workerPath, procs, cmdLine
    pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")
    workerPath = fso.BuildPath(scriptDir, "run_scheduler_background.bat")

    If Not fso.FileExists(pythonExe) Then
        StartBackground = "Cannot start: virtual environment not found." & vbCrLf & _
            pythonExe & vbCrLf & vbCrLf & _
            "Create it, then install dependencies:" & vbCrLf & _
            "    py -m venv .venv" & vbCrLf & _
            "    .venv\Scripts\python -m pip install -r requirements.txt"
        Exit Function
    End If
    If Not fso.FileExists(workerPath) Then
        StartBackground = "Cannot start: missing " & workerPath
        Exit Function
    End If

    Set procs = FindSchedulerProcesses()
    If procs.Count > 0 Then
        StartBackground = "Scheduler is already running (pid " & JoinIds(procs) & ")."
        Exit Function
    End If

    shell.CurrentDirectory = scriptDir
    cmdLine = "cmd.exe /c " & quoteChar & quoteChar & workerPath & quoteChar & " hidden" & quoteChar
    shell.Run cmdLine, 0, False

    WScript.Sleep 1500
    Set procs = FindSchedulerProcesses()
    If procs.Count = 0 Then
        StartBackground = "Scheduler was launched but is not running." & vbCrLf & _
            "It may have exited early; recent log lines:" & vbCrLf & vbCrLf & _
            TailLines(logPath, 8)
    Else
        StartBackground = "Scheduler started in the background (pid " & JoinIds(procs) & ")." & vbCrLf & _
            "Log: " & logPath
    End If
End Function

Function StopScheduler(interactive)
    Dim procs, pid, remaining
    Set procs = FindSchedulerProcesses()
    If procs.Count = 0 Then
        StopScheduler = "Scheduler is not running."
        Exit Function
    End If

    If interactive Then
        If MsgBox("Stop the scheduler (pid " & JoinIds(procs) & ")?", vbYesNo + vbQuestion, APP_TITLE) = vbNo Then
            StopScheduler = "Stop cancelled."
            Exit Function
        End If
    End If

    For Each pid In procs.Keys
        On Error Resume Next
        shell.Run "taskkill /T /F /PID " & pid, 0, True
        On Error GoTo 0
    Next

    WScript.Sleep 800
    Set remaining = FindSchedulerProcesses()
    If remaining.Count = 0 Then
        StopScheduler = "Scheduler stopped."
    Else
        StopScheduler = "Stop requested, but these processes are still running: " & JoinIds(remaining) & vbCrLf & _
            "You can also end them from Task Manager."
    End If
End Function

Function StatusText()
    Dim procs, message, tail, lnkPath
    message = "paper-notifier scheduler status" & vbCrLf & vbCrLf

    Set procs = FindSchedulerProcesses()
    If procs.Count > 0 Then
        message = message & "State: RUNNING (pid " & JoinIds(procs) & ")" & vbCrLf
    Else
        message = message & "State: not running" & vbCrLf
    End If

    lnkPath = StartupLnkPath()
    If Len(lnkPath) > 0 And fso.FileExists(lnkPath) Then
        message = message & "Autostart at logon: enabled" & vbCrLf
    Else
        message = message & "Autostart at logon: disabled" & vbCrLf
    End If
    message = message & "Log: " & logPath

    tail = TailLines(logPath, 8)
    If Len(tail) > 0 Then
        message = message & vbCrLf & vbCrLf & "Last log lines:" & vbCrLf & tail
    End If

    StatusText = message
End Function

Function AutostartOn()
    Dim lnkPath, lnk, message
    lnkPath = StartupLnkPath()
    If Len(lnkPath) = 0 Then
        AutostartOn = "Cannot enable autostart: the Startup folder was not found."
        Exit Function
    End If

    On Error Resume Next
    Set lnk = shell.CreateShortcut(lnkPath)
    If Err.Number <> 0 Then
        AutostartOn = "Cannot enable autostart: " & Err.Description
        Err.Clear
        Exit Function
    End If
    lnk.TargetPath = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
    lnk.Arguments = quoteChar & WScript.ScriptFullName & quoteChar & " start-quiet"
    lnk.WorkingDirectory = scriptDir
    lnk.Description = "Start the paper-notifier daily scheduler hidden in the background"
    lnk.WindowStyle = 7
    lnk.Save
    If Err.Number <> 0 Then
        AutostartOn = "Cannot enable autostart: " & Err.Description
        Err.Clear
        Exit Function
    End If
    On Error GoTo 0

    message = "Autostart at logon enabled." & vbCrLf & "Shortcut: " & lnkPath

    If Not IsSchedulerRunning() Then
        message = message & vbCrLf & vbCrLf & StartBackground()
    End If
    AutostartOn = message
End Function

Function AutostartOff()
    Dim lnkPath, message
    lnkPath = StartupLnkPath()
    If Len(lnkPath) = 0 Or Not fso.FileExists(lnkPath) Then
        AutostartOff = "Autostart was not enabled."
        Exit Function
    End If
    On Error Resume Next
    fso.DeleteFile lnkPath, True
    If Err.Number <> 0 Then
        AutostartOff = "Could not delete " & lnkPath & " (" & Err.Description & ")"
        Err.Clear
    Else
        AutostartOff = "Autostart at logon disabled." & vbCrLf & "Removed: " & lnkPath
    End If
    On Error GoTo 0
End Function

Function IsSchedulerRunning()
    IsSchedulerRunning = (FindSchedulerProcesses().Count > 0)
End Function

Function FindSchedulerProcesses()
    Dim result, wmi, col, p, cl, isScheduler
    Set result = CreateObject("Scripting.Dictionary")

    On Error Resume Next
    Set wmi = GetObject("winmgmts:\\.\root\cimv2")
    Set col = wmi.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'cmd.exe'")
    For Each p In col
        cl = ""
        If Not IsNull(p.CommandLine) Then cl = LCase(p.CommandLine)
        isScheduler = False
        If InStr(cl, "paper_notifier.cli") > 0 And InStr(cl, "--schedule") > 0 Then
            isScheduler = True
        End If
        If InStr(cl, "run_scheduler_background") > 0 Then
            isScheduler = True
        End If
        If isScheduler Then
            result.Add p.ProcessId, p
        End If
    Next
    On Error GoTo 0

    Set FindSchedulerProcesses = result
End Function

Function JoinIds(procs)
    Dim pid, s
    s = ""
    For Each pid In procs.Keys
        If Len(s) > 0 Then s = s & ", "
        s = s & pid
    Next
    JoinIds = s
End Function

Function StartupLnkPath()
    Dim dir
    dir = ""
    On Error Resume Next
    dir = shell.SpecialFolders("Startup")
    On Error GoTo 0
    If Len(dir) = 0 Then
        StartupLnkPath = ""
    Else
        StartupLnkPath = fso.BuildPath(dir, LNK_NAME)
    End If
End Function

Sub EnsureLogsFolder()
    On Error Resume Next
    If Not fso.FolderExists(fso.BuildPath(scriptDir, "logs")) Then
        fso.CreateFolder fso.BuildPath(scriptDir, "logs")
    End If
    On Error GoTo 0
End Sub

Function TailLines(path, lineCount)
    Dim text, lines, startIndex, i, line, output, stream
    TailLines = ""
    If Not fso.FileExists(path) Then Exit Function

    On Error Resume Next
    Set stream = fso.OpenTextFile(path, 1)
    text = stream.ReadAll
    stream.Close
    On Error GoTo 0
    If Len(text) = 0 Then Exit Function

    lines = Split(text, vbLf)
    startIndex = UBound(lines) - lineCount + 1
    If startIndex < 0 Then startIndex = 0
    output = ""
    For i = startIndex To UBound(lines)
        line = Trim(Replace(lines(i), vbCr, ""))
        If Len(line) > 95 Then line = Left(line, 92) & "..."
        If Len(output) > 0 Then output = output & vbCrLf
        output = output & line
    Next
    TailLines = output
End Function

Function PromptMenu()
    Dim text
    text = "paper-notifier scheduler" & vbCrLf & vbCrLf & _
        "1 - Start scheduler in the background" & vbCrLf & _
        "2 - Stop scheduler" & vbCrLf & _
        "3 - Status" & vbCrLf & _
        "4 - Enable autostart at logon" & vbCrLf & _
        "5 - Disable autostart at logon" & vbCrLf & vbCrLf & _
        "0 - Exit" & vbCrLf & vbCrLf & _
        "Enter a number:"
    PromptMenu = Trim(InputBox(text, APP_TITLE, ""))
End Function

Function UsageText()
    UsageText = "Usage: wscript scheduler_control.vbs [action]" & vbCrLf & vbCrLf & _
        "Actions:" & vbCrLf & _
        "  start           start the scheduler hidden in the background" & vbCrLf & _
        "  stop            stop the background scheduler" & vbCrLf & _
        "  status          show running state and recent log lines" & vbCrLf & _
        "  autostart-on    start hidden at every logon" & vbCrLf & _
        "  autostart-off   remove the logon autostart" & vbCrLf & vbCrLf & _
        "Without an action the interactive menu is shown."
End Function

Function FirstLine(text)
    Dim pos
    pos = InStr(text, vbCrLf)
    If pos > 0 Then
        FirstLine = Left(text, pos - 1)
    Else
        FirstLine = text
    End If
End Function

Sub LogLine(text)
    Dim stream
    On Error Resume Next
    Set stream = fso.OpenTextFile(logPath, 8, True)
    stream.WriteLine "[" & Timestamp() & "] " & text
    stream.Close
    On Error GoTo 0
End Sub

Function Timestamp()
    Dim d
    d = Now
    Timestamp = Year(d) & "-" & Pad2(Month(d)) & "-" & Pad2(Day(d)) & " " & _
        Pad2(Hour(d)) & ":" & Pad2(Minute(d)) & ":" & Pad2(Second(d))
End Function

Function Pad2(value)
    Pad2 = Right("0" & value, 2)
End Function
