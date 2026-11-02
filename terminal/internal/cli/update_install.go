package cli

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

type updateFile struct {
	path, source, staged, backup string
	contents                     []byte
	mode                         os.FileMode
}

// Prepare every executable and marker, plus same-directory backups, before
// changing any installed path. In particular a full/read-only custom bin volume
// must fail before the root executable has been replaced.
func installUpdatedBinary(source string, old, next installation) error {
	metadata, e := json.Marshal(next)
	if e != nil {
		return configError("无法编码更新记录")
	}
	metadata = append(metadata, '\n')
	files := []updateFile{{path: filepath.Join(next.Root, executableName()), source: source, mode: 0755}}
	if runtime.GOOS == "windows" && next.Launcher != files[0].path {
		files = append(files, updateFile{path: next.Launcher, source: source, mode: 0755})
	}
	files = append(files, updateFile{path: filepath.Join(next.Root, installMarker), contents: metadata, mode: 0600})
	if runtime.GOOS == "windows" && next.Launcher != files[0].path {
		files = append(files, updateFile{path: next.Launcher + ".install.json", contents: metadata, mode: 0600})
	}
	paths := []string{}
	for _, f := range files {
		paths = append(paths, f.path)
	}
	// A cheap guard catches a busy Windows command without writing staging files.
	release, e := lockUpdateTargets(paths...)
	if e != nil {
		return e
	}
	release()
	keepBackups := false
	defer func() {
		for _, f := range files {
			if f.staged != "" {
				_ = os.Remove(f.staged)
			}
			if f.backup != "" && !keepBackups {
				_ = os.Remove(f.backup)
			}
		}
	}()
	for i := range files {
		f := &files[i]
		st, e := os.Lstat(f.path)
		if e != nil || !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 {
			return configError("更新目标不存在或不是普通文件，原安装未修改")
		}
		f.staged, e = stageUpdateFile(f.path, f.source, f.contents, f.mode)
		if e != nil {
			return e
		}
		f.backup, e = stageUpdateFile(f.path, f.path, nil, st.Mode().Perm())
		if e != nil {
			return e
		}
		if f.source != "" && !binaryMatches(f.backup, old.Digest) {
			return configError("更新期间安装程序发生变化，原安装未修改")
		}
		paths = append(paths, f.staged, f.backup)
	}
	// A different update must not have committed while we were preparing files.
	marker, e := readInstallation(filepath.Join(next.Root, installMarker))
	if e != nil || marker == nil || *marker != old {
		return configError("更新期间安装记录发生变化，请重新检查版本")
	}
	// Also hold the staged and backup files on Windows. Handles follow renames,
	// preventing other processes from opening a half-committed installation.
	release, e = lockUpdateTargets(paths...)
	if e != nil {
		return e
	}
	defer release()
	for i := range files {
		if e = renameUpdateFile(files[i].staged, files[i].path); e != nil {
			rollbackOK := true
			for j := i - 1; j >= 0; j-- {
				if renameUpdateFile(files[j].backup, files[j].path) != nil {
					rollbackOK = false
				}
			}
			if rollbackOK {
				return &failure{"更新替换失败，已恢复原安装；请检查目录权限与磁盘空间", "update_install_failed", 1}
			}
			keepBackups = true
			return &failure{"更新替换和恢复失败，请从门户重新安装修复；原程序目录内 .orbit-update-* 备份已保留，配置和登录文件未修改", "update_recovery_required", 1}
		}
	}
	return nil
}

func stageUpdateFile(destination, source string, contents []byte, mode os.FileMode) (string, error) {
	f, e := os.CreateTemp(filepath.Dir(destination), ".orbit-update-*")
	if e != nil {
		return "", configError("更新暂存失败，请检查目录权限与空间；原安装未修改")
	}
	path := f.Name()
	keep := false
	defer func() {
		_ = f.Close()
		if !keep {
			_ = os.Remove(path)
		}
	}()
	if e = f.Chmod(mode); e == nil {
		if source != "" {
			var in *os.File
			in, e = os.Open(source)
			if e == nil {
				_, e = io.Copy(f, in)
				_ = in.Close()
			}
		} else {
			_, e = f.Write(contents)
		}
	}
	if e == nil {
		e = f.Sync()
	}
	if ce := f.Close(); e == nil {
		e = ce
	}
	if e != nil {
		return "", configError("更新暂存失败，请检查目录权限与空间；原安装未修改")
	}
	keep = true
	return path, nil
}
