package cli

import (
	"encoding/binary"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unsafe"
)

func windowsUpdatePath(path string) string {
	absolute, _ := filepath.Abs(path)
	if strings.HasPrefix(absolute, `\\?\`) {
		return absolute
	}
	if strings.HasPrefix(absolute, `\\`) {
		return `\\?\UNC\` + strings.TrimPrefix(absolute, `\\`)
	}
	return `\\?\` + absolute
}

// MoveFileEx (used by os.Rename) cannot replace our guarded open files. The
// Windows FileRenameInfoEx POSIX operation permits replacement while preserving
// every guard until the whole transaction completes. No extra runtime required.
func renameUpdateFile(source, target string) error {
	name, e := syscall.UTF16PtrFromString(windowsUpdatePath(source))
	if e != nil {
		return e
	}
	h, e := syscall.CreateFile(name, 0x10000, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if e != nil {
		return e
	}
	defer syscall.CloseHandle(h)
	wide, e := syscall.UTF16FromString(windowsUpdatePath(target))
	if e != nil {
		return e
	}
	type renameInfo struct {
		flags  uint32
		root   syscall.Handle
		length uint32
		name   [1]uint16
	}
	var layout renameInfo
	offset := int(unsafe.Offsetof(layout.name))
	length := (len(wide) - 1) * 2
	buffer := make([]byte, offset+length+2)
	binary.LittleEndian.PutUint32(buffer, 3) // REPLACE_IF_EXISTS | POSIX_SEMANTICS
	binary.LittleEndian.PutUint32(buffer[int(unsafe.Offsetof(layout.length)):], uint32(length))
	for i, r := range wide {
		binary.LittleEndian.PutUint16(buffer[offset+i*2:], r)
	}
	result, _, callErr := kernelDLL.NewProc("SetFileInformationByHandle").Call(uintptr(h), 22, uintptr(unsafe.Pointer(&buffer[0])), uintptr(len(buffer)))
	runtime.KeepAlive(buffer)
	if result == 0 {
		return os.NewSyscallError("SetFileInformationByHandle", callErr)
	}
	return nil
}

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
		name, e := syscall.UTF16PtrFromString(windowsUpdatePath(path))
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
