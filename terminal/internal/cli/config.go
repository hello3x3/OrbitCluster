package cli

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode"
)

type failure struct {
	Message, Code string
	Exit          int
}

func (e *failure) Error() string { return e.Message }
func configError(s string) error { return &failure{s, "configuration_error", 2} }
func authError() error {
	return &failure{"请运行 orbit login --username 用户名，认证到期后需重新登录", "authentication_required", 3}
}

type settings struct {
	Root, Server, CA string
	Timeout, Poll    float64
	AllowHTTP        bool
}

func normalizeServer(s string, allow bool) (string, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", nil
	}
	u, e := url.Parse(s)
	if e != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || strings.ContainsAny(s, "?#\\") || strings.IndexFunc(s, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) >= 0 {
		return "", configError("服务地址需为 HTTP 或 HTTPS，不能包含账号、密码、查询参数或片段")
	}
	if u.Scheme == "http" && !allow {
		return "", configError("当前配置仅允许 HTTPS；将 allow_http 设为 true 可使用内网 HTTP")
	}
	if p := u.Port(); p != "" {
		n, e := strconv.Atoi(p)
		if e != nil || n < 1 || n > 65535 {
			return "", configError("服务端口无效")
		}
	}
	u.Host = strings.ToLower(u.Host)
	u.Path = strings.TrimRight(u.Path, "/")
	return strings.TrimRight(u.String(), "/"), nil
}
func defaultRoot() string {
	if x := os.Getenv("ORBIT_CONFIG_DIR"); x != "" {
		return x
	}
	x := os.Getenv("XDG_CONFIG_HOME")
	if x == "" {
		h, _ := os.UserHomeDir()
		x = filepath.Join(h, ".config")
	}
	return filepath.Join(x, "orbitcluster")
}
func readObject(p string, private bool) (map[string]any, error) {
	st, e := os.Lstat(p)
	if os.IsNotExist(e) {
		return map[string]any{}, nil
	}
	if e != nil {
		return nil, configError("无法读取本机配置文件：" + p)
	}
	if st.Mode()&os.ModeSymlink != 0 || !st.Mode().IsRegular() {
		return nil, configError("拒绝读取符号链接或非普通文件：" + p)
	}
	if private {
		if e = checkPrivate(p, st); e != nil {
			return nil, e
		}
	}
	b, e := os.ReadFile(p)
	if e != nil {
		return nil, configError("无法读取本机配置文件：" + p)
	}
	var d map[string]any
	if json.Unmarshal(b, &d) != nil || d == nil {
		return nil, configError("本机 JSON 文件格式错误：" + p)
	}
	return d, nil
}
func privateRoot(root string) error {
	if st, e := os.Lstat(root); e == nil {
		if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return configError("配置目录不能为符号链接或普通文件")
		}
		if e = checkOwner(root, st); e != nil {
			return e
		}
	} else if !os.IsNotExist(e) {
		return configError("无法访问配置目录")
	}
	if os.MkdirAll(root, 0700) != nil {
		return configError("无法创建配置目录：" + root)
	}
	return secureRoot(root)
}
func writeObject(root, name string, d any) error {
	if e := privateRoot(root); e != nil {
		return e
	}
	p := filepath.Join(root, name)
	if st, e := os.Lstat(p); e == nil && (!st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0) {
		return configError("拒绝覆盖符号链接或非普通文件：" + p)
	}
	b, e := json.MarshalIndent(d, "", "  ")
	if e != nil {
		return configError("无法编码配置")
	}
	f, e := os.CreateTemp(root, ".orbit-*.tmp")
	if e != nil {
		return configError("无法写入配置目录")
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(append(b, '\n'))
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e == nil {
		e = os.Rename(tmp, p)
	}
	if e != nil {
		return configError("无法保存配置文件：" + p)
	}
	return nil
}
func asBool(v any) (bool, error) {
	if b, ok := v.(bool); ok {
		return b, nil
	}
	switch strings.ToLower(fmt.Sprint(v)) {
	case "1", "true", "yes":
		return true, nil
	case "0", "false", "no":
		return false, nil
	}
	return false, configError("布尔配置只能为 true/false 或 1/0")
}
func asNumber(v any) (float64, error) {
	n, e := strconv.ParseFloat(fmt.Sprint(v), 64)
	if e != nil || !(n > 0 && n <= 3600) {
		return 0, configError("超时和刷新间隔需大于 0 且不超过 3600 秒")
	}
	return n, nil
}
func parseSettings(root string, d map[string]any) (settings, error) {
	s := settings{Root: root, Timeout: 120, Poll: 5, AllowHTTP: true}
	var e error
	if v, ok := d["allow_http"]; ok {
		s.AllowHTTP, e = asBool(v)
		if e != nil {
			return s, e
		}
	}
	if v, ok := d["server"]; ok {
		s.Server, e = normalizeServer(fmt.Sprint(v), s.AllowHTTP)
		if e != nil {
			return s, e
		}
	}
	if v, ok := d["timeout"]; ok {
		s.Timeout, e = asNumber(v)
		if e != nil {
			return s, e
		}
	}
	if v, ok := d["poll_seconds"]; ok {
		s.Poll, e = asNumber(v)
		if e != nil {
			return s, e
		}
	}
	if v := d["ca_bundle"]; v != nil && fmt.Sprint(v) != "" {
		s.CA = fmt.Sprint(v)
		if strings.HasPrefix(s.CA, "~/") {
			h, _ := os.UserHomeDir()
			s.CA = filepath.Join(h, s.CA[2:])
		}
		s.CA, _ = filepath.Abs(s.CA)
		if st, e := os.Stat(s.CA); e != nil || !st.Mode().IsRegular() {
			return s, configError("CA 公共证书文件不存在")
		}
	}
	return s, nil
}
func loadSettings(a arguments) (settings, error) {
	root := defaultRoot()
	if os.Getenv("ORBIT_CONFIG_DIR") == "" && a.Val["config-dir"] == "" {
		if exe, e := os.Executable(); e == nil {
			if m, e := findInstallation(exe); e != nil {
				return settings{}, e
			} else if m != nil {
				root = m.Config
			}
		}
	}
	if a.Val["config-dir"] != "" {
		root = a.Val["config-dir"]
	}
	if st, e := os.Lstat(root); e == nil && st.Mode()&os.ModeSymlink != 0 {
		return settings{}, configError("配置目录不能为符号链接")
	}
	root, _ = canonicalPath(root)
	d, e := readObject(filepath.Join(root, "config.json"), false)
	if e != nil {
		return settings{}, e
	}
	for _, k := range []string{"server", "ca_bundle", "timeout", "poll_seconds", "allow_http"} {
		if v := os.Getenv("ORBIT_" + strings.ToUpper(k)); v != "" {
			d[k] = v
		}
	}
	for flag, key := range map[string]string{"server": "server", "ca-bundle": "ca_bundle", "request-timeout": "timeout", "poll-seconds": "poll_seconds"} {
		if v, ok := a.Val[flag]; ok {
			d[key] = v
		}
	}
	if a.Bool["allow-http"] {
		d["allow_http"] = true
	}
	if a.Bool["require-https"] {
		d["allow_http"] = false
	}
	return parseSettings(root, d)
}
func (s settings) save(key, value string) error {
	allowed := map[string]bool{"server": true, "ca_bundle": true, "timeout": true, "poll_seconds": true, "allow_http": true}
	if !allowed[key] {
		return configError("未知配置项")
	}
	d, e := readObject(filepath.Join(s.Root, "config.json"), false)
	if e != nil {
		return e
	}
	d[key] = value
	if _, e = parseSettings(s.Root, d); e != nil {
		return e
	}
	return writeObject(s.Root, "config.json", d)
}
func (s settings) public() any {
	return map[string]any{"server": s.Server, "ca_bundle": s.CA, "timeout": s.Timeout, "poll_seconds": s.Poll, "allow_http": s.AllowHTTP, "config_dir": s.Root}
}

// Resolve existing parent aliases as well as paths created by this installation.
func canonicalPath(p string) (string, error) {
	absolute, e := filepath.Abs(p)
	if e != nil {
		return "", e
	}
	parent := absolute
	for {
		if resolved, e := filepath.EvalSymlinks(parent); e == nil {
			rel, _ := filepath.Rel(parent, absolute)
			return filepath.Join(resolved, rel), nil
		}
		next := filepath.Dir(parent)
		if next == parent {
			return absolute, nil
		}
		parent = next
	}
}
