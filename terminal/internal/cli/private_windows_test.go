package cli

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestWindowsPrivateDACLRemovesExplicitOtherUsers(t *testing.T) {
	root := t.TempDir()
	if e := exec.Command("icacls", root, "/grant", "*S-1-1-0:(OI)(CI)RX").Run(); e != nil {
		t.Fatal(e)
	}
	if e := secureRoot(root); e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(root, "auth.json")
	if e := os.WriteFile(path, []byte(`{"sessions":{}}`), 0600); e != nil {
		t.Fatal(e)
	}
	st, _ := os.Stat(path)
	if e := checkPrivate(path, st); e != nil {
		t.Fatal("explicit Everyone grant survived private root", e)
	}
	if e := exec.Command("icacls", path, "/grant", "*S-1-1-0:R").Run(); e != nil {
		t.Fatal(e)
	}
	if e := checkPrivate(path, st); e == nil {
		t.Fatal("permissive credential accepted")
	}
}

func TestWindowsPowerShellInstallerUnicodeLifecycle(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "source.exe")
	if b, e := exec.Command("go", "build", "-o", exe, "../../cmd/orbit").CombinedOutput(); e != nil {
		t.Fatal(e, string(b))
	}
	var compressed bytes.Buffer
	z := zip.NewWriter(&compressed)
	entry, _ := z.Create("orbit.exe")
	b, _ := os.ReadFile(exe)
	_, _ = entry.Write(b)
	_ = z.Close()
	data := compressed.Bytes()
	digest := fmt.Sprintf("%x", sha256.Sum256(data))
	name := "orbit-" + Version + "-windows-" + runtime.GOARCH + ".zip"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/terminal/manifest.json":
			fmt.Fprintf(w, `{"version":%q}`, Version)
		case "/terminal/downloads/" + name:
			_, _ = w.Write(data)
		case "/terminal/downloads/" + name + ".sha256":
			fmt.Fprintln(w, digest+"  "+name)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	script, _ := filepath.Abs("../../install.ps1")
	root := filepath.Join(dir, "安装目录 中文")
	bin := filepath.Join(dir, "命令目录 中文")
	config := filepath.Join(dir, "配置 中文")
	command := exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Server", server.URL, "-InstallDir", root, "-BinDir", bin, "-ConfigDir", config)
	command.Env = append(os.Environ(), "ORBIT_CONFIG_DIR=", "ORBIT_SERVER=", "ORBIT_CA_BUNDLE=", "ORBIT_ALLOW_HTTP=")
	output, e := command.CombinedOutput()
	if e != nil {
		t.Fatal(e, string(output))
	}
	for i := 1; i <= 5; i++ {
		if !bytes.Contains(output, []byte(fmt.Sprintf("[%d/5]", i))) {
			t.Fatal("missing installation progress", string(output))
		}
	}
	launcher := filepath.Join(bin, "orbit.exe")
	show := exec.Command(launcher, "config", "show", "--json")
	show.Env = command.Env
	output, e = show.CombinedOutput()
	if e != nil || !bytes.Contains(output, []byte(server.URL)) {
		t.Fatal(e, string(output))
	}
	remove := exec.Command(launcher, "uninstall", "--yes", "--purge", "--json")
	remove.Env = command.Env
	if output, e = remove.CombinedOutput(); e != nil {
		t.Fatal(e, string(output))
	}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		if _, e = os.Stat(filepath.Join(root, "orbit.exe")); os.IsNotExist(e) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	for _, path := range []string{launcher, filepath.Join(root, "orbit.exe"), filepath.Join(bin, "orbit.exe.install.json"), filepath.Join(config, "config.json")} {
		if _, e = os.Lstat(path); !os.IsNotExist(e) {
			t.Fatal("uninstall did not remove Unicode path", path, e)
		}
	}
}
