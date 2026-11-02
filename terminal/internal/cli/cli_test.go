package cli

import (
	"bytes"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func testArgs(t *testing.T, server string) arguments {
	t.Helper()
	t.Setenv("ORBIT_CONFIG_DIR", t.TempDir())
	t.Setenv("ORBIT_SERVER", server)
	t.Setenv("ORBIT_ALLOW_HTTP", "")
	a, _ := parse(nil)
	return a
}
func TestHTTPDefaultAndExplicitStrict(t *testing.T) {
	a := testArgs(t, "http://10.71.106.251")
	s, e := loadSettings(a)
	if e != nil || !s.AllowHTTP {
		t.Fatal(e, s)
	}
	a.Bool["require-https"] = true
	if _, e = loadSettings(a); e == nil {
		t.Fatal("explicit HTTPS-only ignored")
	}
	for _, bad := range []string{"http://u:p@host", "http://host?x=secret", "http://host#x", "file:///tmp/x", "http://host\n/evil", "http://host:99999"} {
		if _, e = normalizeServer(bad, true); e == nil {
			t.Fatal(bad)
		}
	}
}
func TestAuthNoCookiesNoSecretsAndExactOrigin(t *testing.T) {
	token := strings.Repeat("T", 43)
	seen := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen++
		if r.Header.Get("Cookie") != "" {
			t.Error("cookie sent")
		}
		w.Header().Set("Set-Cookie", "unwanted=secret")
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == prefix+"/auth/login" {
			if r.Header.Get("Authorization") != "" {
				t.Error("token sent with login")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": map[string]any{"token": token, "username": "alice", "issued_at": time.Now().Unix(), "expires_at": time.Now().Unix() + 604800}})
		} else {
			if r.Header.Get("Authorization") != "Bearer "+token {
				t.Error("wrong bearer")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": map[string]any{"username": "alice"}})
		}
	}))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	d, e := c.login("alice", "NeverPrintMe", "")
	if e != nil {
		t.Fatal(e)
	}
	b, _ := json.Marshal(d)
	if bytes.Contains(b, []byte(token)) || bytes.Contains(b, []byte("NeverPrintMe")) {
		t.Fatal("secret emitted")
	}
	if _, e = c.request("GET", "/auth/me", nil, nil, true); e != nil {
		t.Fatal(e)
	}
	c.s.Server = strings.Replace(srv.URL, "http:", "https:", 1)
	if _, e = c.request("GET", "/auth/me", nil, nil, true); e == nil {
		t.Fatal("cross-origin auth reused")
	}
	if seen != 2 {
		t.Fatal(seen)
	}
}
func TestExpiryAndRedirectNeverSendNewRequest(t *testing.T) {
	seen := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen++
		http.Redirect(w, r, "http://example.invalid/steal", 302)
	}))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	e := c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() - 1}, false)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.request("GET", "/auth/me", nil, nil, true); e == nil {
		t.Fatal("expired credential used")
	}
	if seen != 0 {
		t.Fatal(seen)
	}
	_ = c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() + 100}, false)
	_, e = c.request("GET", "/auth/me", nil, nil, true)
	if f, ok := e.(*failure); !ok || f.Code != "unexpected_redirect" {
		t.Fatal(e)
	}
	if seen != 1 {
		t.Fatal(seen)
	}
}
func TestTLSRequiresTrustedCA(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"ok":true,"data":[]}`)) }))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	if _, e := c.request("GET", "/plans", nil, nil, false); e == nil {
		t.Fatal("untrusted certificate accepted")
	}
	der := srv.Certificate().Raw
	if _, e := x509.ParseCertificate(der); e != nil {
		t.Fatal(e)
	}
	s.CA = filepath.Join(t.TempDir(), "ca.crt")
	_ = os.WriteFile(s.CA, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0600)
	c, e := newClient(s)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.request("GET", "/plans", nil, nil, false); e != nil {
		t.Fatal(e)
	}
}
func TestCredentialSymlinkAndUnreadableDirectoryReturnErrors(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permissions and symlink exercise")
	}
	root := t.TempDir()
	victim := filepath.Join(t.TempDir(), "victim")
	_ = os.WriteFile(victim, []byte("original"), 0600)
	_ = os.Symlink(victim, filepath.Join(root, "auth.json"))
	if _, e := readObject(filepath.Join(root, "auth.json"), true); e == nil {
		t.Fatal("symlink read")
	}
	if e := writeObject(root, "auth.json", map[string]any{}); e == nil {
		t.Fatal("symlink overwritten")
	}
	b, _ := os.ReadFile(victim)
	if string(b) != "original" {
		t.Fatal("victim changed")
	}
}
func TestCommandsEncodeExistingAPI(t *testing.T) {
	type call struct {
		method, path string
		form         map[string][]string
	}
	var calls []call
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		calls = append(calls, call{r.Method, r.URL.Path, r.PostForm})
		var d any = map[string]any{"instance_id": 123}
		switch r.URL.Path {
		case prefix + "/plans":
			d = []any{map[string]any{"id": 1, "name": "gpu-1"}}
		case prefix + "/images":
			d = map[string]any{"public": []any{map[string]any{"name": "cuda.sqsh", "path": "/images/cuda.sqsh"}}, "mine": []any{map[string]any{"name": "mine.sqsh", "path": "/images/alice/mine.sqsh"}}}
		case prefix + "/profile":
			d = map[string]any{"keys": []any{}}
		case prefix + "/instances/123/connection":
			d = map[string]any{"ready": true, "state": "RUNNING"}
		case prefix + "/instances/123":
			d = map[string]any{"state": "CANCELLED", "last_save": "保存成功", "saving_now": false}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": d})
	}))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	_ = c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() + 604800}, false)
	tests := []struct {
		args         []string
		method, path string
		field, value string
	}{
		{[]string{"instances", "create", "--name", "exp", "--plan", "gpu-1", "--image", "cuda", "--hours", "4"}, "POST", "/instances", "image", "/images/cuda.sqsh"},
		{[]string{"instances", "stop", "123", "--yes"}, "POST", "/instances/123/stop", "", ""},
		{[]string{"instances", "restart", "123"}, "POST", "/instances/123/restart", "", ""},
		{[]string{"instances", "logs", "123", "--lines", "12"}, "GET", "/instances/123/log", "", ""},
		{[]string{"images", "save", "--instance", "123", "--name", "new"}, "POST", "/instances/123/save-image", "name", "new"},
		{[]string{"images", "rename", "mine", "renamed"}, "POST", "/images/mine/rename", "new_name", "renamed"},
		{[]string{"images", "comment", "mine", "note"}, "POST", "/images/mine/comment", "note", "note"},
		{[]string{"profile", "name", "Alice"}, "POST", "/profile/name", "display_name", "Alice"},
		{[]string{"keys", "delete", "5", "--yes"}, "POST", "/profile/keys/5/delete", "", ""},
		{[]string{"instances", "wait", "123", "--until", "ssh-ready"}, "GET", "/instances/123/connection", "", ""},
		{[]string{"instances", "wait", "123", "--until", "saved"}, "GET", "/instances/123", "", ""},
	}
	for _, tt := range tests {
		t.Run(strings.Join(tt.args, " "), func(t *testing.T) {
			var out, err bytes.Buffer
			code := Run(append(tt.args, "--json"), strings.NewReader(""), &out, &err)
			if code != 0 {
				t.Fatal(code, err.String())
			}
			last := calls[len(calls)-1]
			if last.method != tt.method || last.path != prefix+tt.path {
				t.Fatal(last)
			}
			if tt.field != "" && last.form[tt.field][0] != tt.value {
				t.Fatal(last)
			}
		})
	}
	count := len(calls)
	var out, err bytes.Buffer
	if Run([]string{"instances", "delete", "123", "--json"}, strings.NewReader(""), &out, &err) != 2 {
		t.Fatal("unconfirmed delete")
	}
	if len(calls) != count {
		t.Fatal("delete reached server")
	}
}
func TestNoTUIAndConfigJSON(t *testing.T) {
	testArgs(t, "")
	for _, args := range [][]string{nil, {"--help"}, {"instances", "create", "--help"}} {
		var out, err bytes.Buffer
		if Run(args, strings.NewReader(""), &out, &err) != 0 || !strings.Contains(out.String(), "--json") {
			t.Fatal(out.String(), err.String())
		}
	}
	var out, err bytes.Buffer
	if Run([]string{"tui", "--json"}, strings.NewReader(""), &out, &err) != 2 || out.Len() != 0 || !strings.Contains(err.String(), "已移除") {
		t.Fatal(out.String(), err.String())
	}
	if Run([]string{"config", "set", "server", "http://10.71.106.251", "--json"}, strings.NewReader(""), &out, &err) != 0 {
		t.Fatal(err.String())
	}
}
func TestRejectExtraParametersBeforeWrites(t *testing.T) {
	a := testArgs(t, "http://127.0.0.1:1")
	_ = a
	for _, args := range [][]string{{"instances", "create", "extra", "--name", "exp", "--plan", "1", "--image", "cuda", "--hours", "1"}, {"profile", "show", "extra"}, {"login", "--username", "alice", "--name", "ignored"}} {
		var out, err bytes.Buffer
		if Run(append(args, "--json"), strings.NewReader("secret\n"), &out, &err) != 2 {
			t.Fatal("unexpected args not rejected", args, err.String())
		}
	}
}
func TestInstalledUninstallPreservesUnrelatedFiles(t *testing.T) {
	if testing.Short() {
		t.Skip("binary lifecycle")
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, executableName())
	cmd := exec.Command("go", "build", "-o", exe, "../../cmd/orbit")
	if b, e := cmd.CombinedOutput(); e != nil {
		t.Fatal(e, string(b))
	}
	root := filepath.Join(dir, "install 中文 with spaces")
	bin := filepath.Join(dir, "自定义命令")
	config := filepath.Join(dir, "config")
	run := func(p string, args ...string) ([]byte, error) {
		c := exec.Command(p, args...)
		c.Env = append(os.Environ(), "ORBIT_CONFIG_DIR=", "HOME="+dir, "USERPROFILE="+dir, "XDG_CONFIG_HOME="+filepath.Join(dir, "xdg"), "ORBIT_SERVER=", "ORBIT_CA_BUNDLE=", "ORBIT_ALLOW_HTTP=")
		return c.CombinedOutput()
	}
	if b, e := run(exe, "setup", "--server", "http://10.71.106.251", "--install-dir", root, "--bin-dir", bin, "--config-dir", config); e != nil {
		t.Fatal(e, string(b))
	}
	if b, e := run(filepath.Join(bin, executableName()), "config", "show", "--json"); e != nil || !strings.Contains(string(b), "10.71.106.251") {
		t.Fatal("installed config forgotten", e, string(b))
	}
	_ = os.WriteFile(filepath.Join(config, "keep.txt"), []byte("mine"), 0600)
	launcher := filepath.Join(bin, executableName())
	if b, e := run(launcher, "--version"); e != nil || !strings.Contains(string(b), Version) {
		t.Fatal(e, string(b))
	}
	if b, e := run(launcher, "uninstall", "--yes", "--purge"); e != nil {
		t.Fatal(e, string(b))
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if _, e := os.Stat(launcher); os.IsNotExist(e) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	for _, p := range []string{launcher, filepath.Join(root, executableName()), filepath.Join(config, "config.json")} {
		if _, e := os.Lstat(p); !os.IsNotExist(e) {
			t.Fatal("not removed", p, e)
		}
	}
	if b, _ := os.ReadFile(filepath.Join(config, "keep.txt")); string(b) != "mine" {
		t.Fatal("unrelated file removed")
	}
}

func TestImageMutationCannotSelectPublicNamesake(t *testing.T) {
	posts := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" {
			posts++
		}
		data := any(map[string]any{})
		if r.URL.Path == prefix+"/images" {
			data = map[string]any{"public": []any{map[string]any{"name": "cuda.sqsh", "path": "/share/images/cuda.sqsh"}}, "mine": []any{map[string]any{"name": "cuda.sqsh", "path": "/share/images/alice/cuda.sqsh"}}}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": data})
	}))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	_ = c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() + 100}, false)
	var out, err bytes.Buffer
	if code := Run([]string{"images", "delete", "/share/images/cuda.sqsh", "--yes", "--json"}, strings.NewReader(""), &out, &err); code != 2 || posts != 0 {
		t.Fatal("public path mutated private namesake", code, posts, out.String(), err.String())
	}
	out.Reset()
	err.Reset()
	if code := Run([]string{"images", "delete", "cuda", "--yes", "--json"}, strings.NewReader(""), &out, &err); code != 0 || posts != 1 {
		t.Fatal(code, posts, err.String())
	}
}
func TestWaitTimeoutBoundsPoll(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(300 * time.Millisecond)
		_, _ = w.Write([]byte(`{"ok":true,"data":{"state":"RUNNING","ready":true}}`))
	}))
	defer srv.Close()
	a := testArgs(t, srv.URL)
	s, _ := loadSettings(a)
	c, _ := newClient(s)
	_ = c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() + 100}, false)
	a.Val["timeout"] = "0.05"
	start := time.Now()
	_, e := c.wait(a, "1")
	elapsed := time.Since(start)
	if f, ok := e.(*failure); !ok || f.Code != "wait_timeout" || elapsed > 200*time.Millisecond {
		t.Fatal("wait deadline ignored", elapsed, e)
	}
}
func TestSetupRefusesReplacedLauncher(t *testing.T) {
	if testing.Short() {
		t.Skip("binary lifecycle")
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, executableName())
	if b, e := exec.Command("go", "build", "-o", exe, "../../cmd/orbit").CombinedOutput(); e != nil {
		t.Fatal(e, string(b))
	}
	root := filepath.Join(dir, "install")
	bin := filepath.Join(dir, "bin")
	config := filepath.Join(dir, "config")
	args := []string{"setup", "--server", "http://127.0.0.1:1", "--install-dir", root, "--bin-dir", bin, "--config-dir", config}
	run := func() ([]byte, error) {
		c := exec.Command(exe, args...)
		c.Env = append(os.Environ(), "ORBIT_CONFIG_DIR=", "ORBIT_SERVER=", "ORBIT_CA_BUNDLE=")
		return c.CombinedOutput()
	}
	if b, e := run(); e != nil {
		t.Fatal(e, string(b))
	}
	launcher := filepath.Join(bin, executableName())
	_ = os.Remove(launcher)
	_ = os.WriteFile(launcher, []byte("unrelated user program"), 0700)
	if b, e := run(); e == nil {
		t.Fatal("unrelated launcher overwritten", string(b))
	}
	b, _ := os.ReadFile(launcher)
	if string(b) != "unrelated user program" {
		t.Fatal("user file lost")
	}
}
