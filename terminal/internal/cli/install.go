package cli

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const installMarker = ".orbitcluster-install.json"

type installation struct {
	Digest   string `json:"sha256"`
	Schema   int    `json:"schema"`
	Version  string `json:"version"`
	Root     string `json:"install_root"`
	Launcher string `json:"launcher"`
	Config   string `json:"config_dir"`
}

func installDefaults() (string, string) {
	h, _ := os.UserHomeDir()
	if runtime.GOOS == "windows" {
		base := os.Getenv("LOCALAPPDATA")
		if base == "" {
			base = filepath.Join(h, "AppData", "Local")
		}
		root := filepath.Join(base, "OrbitCluster")
		return root, filepath.Join(root, "bin")
	}
	base := os.Getenv("XDG_DATA_HOME")
	if base == "" {
		base = filepath.Join(h, ".local", "share")
	}
	return filepath.Join(base, "orbitcluster"), filepath.Join(h, ".local", "bin")
}
func executableName() string {
	if runtime.GOOS == "windows" {
		return "orbit.exe"
	}
	return "orbit"
}
func safeDirectory(p string) error {
	p, e := filepath.Abs(p)
	if e != nil {
		return configError("安装路径无效")
	}
	h, _ := os.UserHomeDir()
	if p == filepath.VolumeName(p)+string(os.PathSeparator) || p == h || p == filepath.Dir(h) {
		return configError("请指定专用客户端目录")
	}
	if st, e := os.Lstat(p); e == nil {
		if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return configError("安装目录不能为符号链接或普通文件")
		}
		return checkOwner(p, st)
	}
	return nil
}
func copyExecutable(source, target string) error {
	if st, e := os.Lstat(target); e == nil && (!st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0) {
		return configError("拒绝覆盖符号链接或非普通文件")
	}
	in, e := os.Open(source)
	if e != nil {
		return configError("无法读取安装文件")
	}
	defer in.Close()
	f, e := os.CreateTemp(filepath.Dir(target), ".orbit-bin-*")
	if e != nil {
		return configError("无法写入安装目录")
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	_, e = io.Copy(f, in)
	if e == nil {
		e = f.Chmod(0755)
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e == nil {
		e = os.Rename(tmp, target)
	}
	if e != nil {
		return configError("无法替换客户端，请先关闭正在运行的 orbit")
	}
	return nil
}
func setup(a arguments) (any, error) {
	if e := required(a, "server"); e != nil {
		return nil, e
	}
	root, bin := installDefaults()
	if a.Val["install-dir"] != "" {
		root = a.Val["install-dir"]
	}
	if a.Val["bin-dir"] != "" {
		bin = a.Val["bin-dir"]
	}
	if e := safeDirectory(root); e != nil {
		return nil, e
	}
	if e := safeDirectory(bin); e != nil {
		return nil, e
	}
	root, _ = canonicalPath(root)
	bin, _ = canonicalPath(bin)
	for _, p := range []string{root, bin} {
		if e := safeDirectory(p); e != nil {
			return nil, e
		}
	}
	s, e := loadSettings(a)
	if e != nil {
		return nil, e
	}
	s.AllowHTTP = !a.Bool["require-https"]
	s.Server, e = normalizeServer(a.Val["server"], s.AllowHTTP)
	if e != nil {
		return nil, e
	}
	target := filepath.Join(root, executableName())
	launcher := filepath.Join(bin, executableName())
	current, e := os.Executable()
	if e != nil {
		return nil, configError("无法定位当前安装程序")
	}
	digest, e := binaryDigest(current)
	if e != nil {
		return nil, e
	}
	manifest := installation{Schema: 1, Version: Version, Root: root, Launcher: launcher, Config: s.Root, Digest: digest}
	known := false
	legacy := false
	var old installation
	if b, e := os.ReadFile(filepath.Join(root, installMarker)); e == nil {
		if json.Unmarshal(b, &old) != nil || old.Schema != 1 || old.Root != root || old.Launcher != launcher {
			return nil, configError("现有安装标记不匹配，请指定其它安装目录")
		}
		if !binaryMatches(target, old.Digest) {
			return nil, configError("已安装的程序被替换或损坏，拒绝覆盖")
		}
		known = true
	}
	if st, e := os.Lstat(launcher); e == nil {
		if st.Mode()&os.ModeSymlink != 0 {
			dest, _ := os.Readlink(launcher)
			if !known || dest != target {
				return nil, configError("启动器路径已被其它程序占用")
			}
		} else if known {
			if launcher != target && (runtime.GOOS != "windows" || !binaryMatches(launcher, old.Digest)) {
				return nil, configError("启动器已被其它程序替换，拒绝覆盖")
			}
		} else {
			b, e := os.ReadFile(launcher)
			if e != nil || len(b) > 4096 || !strings.Contains(string(b), "# OrbitCluster user terminal launcher") || !strings.Contains(string(b), filepath.Join(root, "venv")) {
				return nil, configError("启动器路径已被其它程序占用")
			}
			legacy = true
		}
	}
	if _, e := os.Lstat(target); e == nil && !known {
		return nil, configError("安装目录内已有未知的 orbit 文件")
	}
	if a.Bool["preserve-config"] {
		if !known {
			return nil, configError("保留配置更新需要已验证的现有安装")
		}
		// Temporary server/config flags select only the update source. They must
		// never change the installed client's persistent settings or credential root.
		manifest.Config = old.Config
		if e = installUpdatedBinary(current, old, manifest); e != nil {
			return nil, e
		}
		return map[string]any{"version": Version, "launcher": launcher, "server": s.Server}, nil
	} else {
		if e = privateRoot(s.Root); e != nil {
			return nil, e
		}
	}
	if e = os.MkdirAll(root, 0755); e != nil {
		return nil, configError("无法创建安装目录")
	}
	if e = os.MkdirAll(bin, 0755); e != nil {
		return nil, configError("无法创建启动器目录")
	}
	if e = copyExecutable(current, target); e != nil {
		return nil, e
	}
	// Construct configuration from validated public settings. Existing auth.json is untouched.
	if !a.Bool["preserve-config"] {
		config := map[string]any{"server": s.Server, "ca_bundle": s.CA, "allow_http": s.AllowHTTP, "timeout": s.Timeout, "poll_seconds": s.Poll}
		if e = writeObject(s.Root, "config.json", config); e != nil {
			return nil, e
		}
	}
	if e = writeObject(root, installMarker, manifest); e != nil {
		return nil, e
	}
	if launcher != target {
		if runtime.GOOS == "windows" {
			if e = copyExecutable(target, launcher); e != nil {
				return nil, e
			}
		} else {
			temp := launcher + fmt.Sprintf(".orbit-%d", os.Getpid())
			defer os.Remove(temp)
			if e = os.Symlink(target, temp); e == nil {
				e = os.Rename(temp, launcher)
			}
			if e != nil {
				return nil, configError("无法安装启动器")
			}
		}
	}
	if runtime.GOOS == "windows" && launcher != target {
		if e = writeInstallation(launcher+".install.json", manifest); e != nil {
			return nil, e
		}
	}
	// A matching old installer marker and a regular pyvenv.cfg identify only the retired client environment.
	if legacy {
		old := filepath.Join(root, "venv")
		st, e := os.Lstat(old)
		cfg, ce := os.Lstat(filepath.Join(old, "pyvenv.cfg"))
		if e == nil && ce == nil && st.IsDir() && st.Mode()&os.ModeSymlink == 0 && cfg.Mode().IsRegular() {
			if e = os.RemoveAll(old); e != nil {
				return nil, configError("新版 CLI 已安装，但旧 Python 环境清理失败，请检查目录权限")
			}
		}
	}

	return map[string]any{"version": Version, "launcher": launcher, "server": s.Server, "legacy_python_removed": legacy, "next": "orbit login --username 用户名"}, nil
}
func uninstall(a arguments) (any, error) {
	exe, e := os.Executable()
	if e != nil {
		return nil, configError("无法定位客户端")
	}
	resolved, e := filepath.EvalSymlinks(exe)
	if e == nil {
		exe = resolved
	}
	m, e := findInstallation(exe)
	if e != nil {
		return nil, e
	}
	if m == nil {
		return nil, configError("当前 orbit 为便携文件或开发版本，无安装标记；请只删除该可执行文件")
	}
	root := m.Root
	marker := filepath.Join(root, installMarker)
	if e = safeDirectory(root); e != nil {
		return nil, e
	}

	target := filepath.Join(root, executableName())
	if exe != target && exe != m.Launcher {
		return nil, configError("当前程序与安装标记不匹配")
	}
	if m.Launcher != target {
		if st, e := os.Lstat(m.Launcher); e == nil {
			if runtime.GOOS != "windows" {
				dest, e := os.Readlink(m.Launcher)
				if e != nil || dest != target {
					return nil, configError("启动器已被其它程序替换，拒绝删除")
				}
			} else if !st.Mode().IsRegular() || !binaryMatches(m.Launcher, m.Digest) {
				return nil, configError("启动器已被其它程序替换")
			}
		}
	}
	if a.Bool["purge"] {
		cr := m.Config
		if a.Val["config-dir"] != "" {
			override, err := canonicalPath(a.Val["config-dir"])
			if err != nil || override != m.Config {
				return nil, configError("卸载清理仅限此安装记录的配置目录，拒绝删除其它目录")
			}
			cr = override
		}
		if e = safeDirectory(cr); e != nil {
			return nil, e
		}
		for _, name := range []string{"config.json", "auth.json"} {
			p := filepath.Join(cr, name)
			if st, e := os.Lstat(p); e == nil {
				if st.Mode()&os.ModeSymlink != 0 || !st.Mode().IsRegular() {
					return nil, configError("配置文件为链接或非普通文件，拒绝清除")
				}
			}
		}
		for _, name := range []string{"config.json", "auth.json"} {
			if e = os.Remove(filepath.Join(cr, name)); e != nil && !os.IsNotExist(e) {
				return nil, configError("无法清除本机配置")
			}
		}
		_ = os.Remove(cr)
	}
	paths := []string{m.Launcher, target, marker}
	if runtime.GOOS == "windows" && m.Launcher != target {
		paths = append(paths, m.Launcher+".install.json")
	}
	if runtime.GOOS == "windows" {
		return scheduleWindowsRemoval(paths, root)
	}
	for _, p := range paths {
		if e = os.Remove(p); e != nil && !os.IsNotExist(e) {
			return nil, configError("卸载失败，请检查本机目录权限")
		}
	}
	_ = os.Remove(root) // Preserve unrecognised files and legacy backups.
	return map[string]any{"uninstalled": true, "config_preserved": !a.Bool["purge"], "server_jobs_unchanged": true}, nil
}
func scheduleWindowsRemoval(paths []string, root string) (any, error) {
	// Defer removal until this executable exits; Windows locks running binaries.
	f, e := os.CreateTemp("", "orbit-uninstall-*.ps1")
	if e != nil {
		return nil, configError("无法创建卸载任务")
	}
	script := f.Name()
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
	body := fmt.Sprintf("Wait-Process -Id %d -ErrorAction SilentlyContinue\n", os.Getpid())
	for _, p := range paths {
		body += "Remove-Item -LiteralPath " + quote(p) + " -Force -ErrorAction SilentlyContinue\n"
	}
	if filepath.Dir(paths[0]) != root {
		body += "try { [IO.Directory]::Delete(" + quote(filepath.Dir(paths[0])) + ") } catch {}\n"
	}
	body += "try { [IO.Directory]::Delete(" + quote(root) + ") } catch {}\nRemove-Item -LiteralPath $PSCommandPath -Force\n"
	_, e = f.WriteString("\ufeff" + body)
	_ = f.Close()
	if e != nil {
		return nil, configError("无法写入卸载任务")
	}
	cmd := exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script)
	if e = cmd.Start(); e != nil {
		return nil, configError("无法启动卸载任务")
	}
	_ = cmd.Process.Release()
	time.Sleep(50 * time.Millisecond)
	return map[string]any{"uninstalled": true, "pending_process_exit": true, "server_jobs_unchanged": true}, nil
}

func binaryDigest(p string) (string, error) {
	f, e := os.Open(p)
	if e != nil {
		return "", configError("无法读取客户端文件")
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return "", configError("无法校验客户端文件")
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func binaryMatches(p, digest string) bool {
	if len(digest) != 64 {
		return false
	}
	d, e := binaryDigest(p)
	return e == nil && d == digest
}
func writeInstallation(path string, m installation) error {
	if st, e := os.Lstat(path); e == nil && (st.Mode()&os.ModeSymlink != 0 || !st.Mode().IsRegular()) {
		return configError("安装标记不能为符号链接")
	}
	b, _ := json.Marshal(m)
	f, e := os.CreateTemp(filepath.Dir(path), ".orbit-marker-*")
	if e != nil {
		return configError("无法写入安装标记")
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	_, e = f.Write(b)
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e == nil {
		e = os.Rename(tmp, path)
	}
	if e != nil {
		return configError("无法保存安装标记")
	}
	return nil
}
func readInstallation(p string) (*installation, error) {
	st, e := os.Lstat(p)
	if os.IsNotExist(e) {
		return nil, nil
	}
	if e != nil || !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 {
		return nil, configError("安装标记不可读取或为链接")
	}
	if e = checkOwner(p, st); e != nil {
		return nil, e
	}
	b, e := os.ReadFile(p)
	if e != nil {
		return nil, configError("无法读取安装标记")
	}
	var m installation
	if json.Unmarshal(b, &m) != nil || m.Schema != 1 || !filepath.IsAbs(m.Root) || !filepath.IsAbs(m.Launcher) || !filepath.IsAbs(m.Config) || len(m.Digest) != 64 {
		return nil, configError("安装标记无效")
	}
	return &m, nil
}
func findInstallation(exe string) (*installation, error) {
	exe, e := canonicalPath(exe)
	if e != nil {
		return nil, e
	}
	for _, p := range []string{filepath.Join(filepath.Dir(exe), installMarker), exe + ".install.json"} {
		m, e := readInstallation(p)
		if e != nil {
			return nil, e
		}
		if m == nil {
			continue
		}
		target := filepath.Join(m.Root, executableName())
		if exe != target && exe != m.Launcher {
			return nil, configError("当前程序与安装标记不匹配")
		}
		rootManifest, e := readInstallation(filepath.Join(m.Root, installMarker))
		if e != nil || rootManifest == nil || *rootManifest != *m {
			return nil, configError("安装目录与启动器标记不匹配")
		}
		if !binaryMatches(exe, m.Digest) {
			return nil, configError("已安装程序与标记不匹配")
		}
		return m, nil
	}
	return nil, nil
}
