package cli

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

// Deny new readers while allowing replacement by rename. A running image or
// existing launcher reader prevents GENERIC_WRITE, before any file is changed.
func lockUpdateTargets(paths ...string) (func(), error) {
	handles := []syscall.Handle{}
	release := func() {
		for _, h := range handles {
			_ = syscall.CloseHandle(h)
		}
	}
	seen := map[string]bool{}
	for _, path := range paths {
		if seen[path] {
			continue
		}
		seen[path] = true
		name, e := syscall.UTF16PtrFromString(path)
		if e != nil {
			release()
			return nil, configError("Windows 更新路径无效")
		}
		h, e := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, syscall.FILE_SHARE_DELETE, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
		if e != nil {
			release()
			return nil, &failure{"其它终端仍在使用 orbit，请结束该命令后再更新", "update_busy", 1}
		}
		handles = append(handles, h)
	}
	return release, nil
}

func applyUpdate(target string, args []string, work string) (bool, error) {
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
	arguments := []string{}
	for _, arg := range args {
		arguments = append(arguments, quote(arg))
	}
	log := filepath.Join(work, "update.log")
	body := "$ErrorActionPreference='Stop'\n"
	body += fmt.Sprintf("$p=Get-Process -Id %d -ErrorAction SilentlyContinue\nif ($p) {$p.WaitForExit()}\n", os.Getpid())
	body += "try {\n $deadline=[DateTime]::UtcNow.AddMinutes(5)\n while ($true) {\n"
	body += " $ErrorActionPreference='Continue'\n"
	body += " try { $result=@(& " + quote(target) + " " + strings.Join(arguments, " ") + " 2>&1); $exitCode=$LASTEXITCODE } finally { $ErrorActionPreference='Stop' }\n"
	body += " $result=[String]::Join([Environment]::NewLine,[string[]]$result)\n"
	body += " [IO.File]::WriteAllText(" + quote(log) + ",$result,[Text.Encoding]::UTF8)\n"
	body += " if ($exitCode -eq 0) {break}\n"
	body += " if (-not $result.Contains('\"code\":\"update_busy\"') -or [DateTime]::UtcNow -ge $deadline) {throw 'CLI 更新未完成；请查看 '+" + quote(log) + "}\n"
	body += " [Threading.Thread]::Sleep(1000)\n }\n"
	body += " Remove-Item -LiteralPath " + quote(work) + " -Recurse -Force\n"
	body += "} catch { [IO.File]::AppendAllText(" + quote(log) + ",$_.ToString(),[Text.Encoding]::UTF8) }\n"
	script := filepath.Join(work, "apply-update.ps1")
	if e := os.WriteFile(script, []byte("\ufeff"+body), 0600); e != nil {
		return false, configError("无法写入 Windows 更新任务")
	}
	cmd := exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script)
	if e := cmd.Start(); e != nil {
		return false, configError("无法启动 Windows 更新任务")
	}
	_ = cmd.Process.Release()
	return true, nil
}
