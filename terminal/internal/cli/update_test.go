package cli

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestNativeUpdateReplacesInstalledBinaryAndPreservesCredentials(t *testing.T) {
	dir := t.TempDir()
	current := filepath.Join(dir, "source-"+executableName())
	next := filepath.Join(dir, "next-"+executableName())
	for _, build := range []struct {
		path  string
		flags string
	}{{current, ""}, {next, "-X=orbitcluster/terminal/internal/cli.Version=9.0.0"}} {
		args := []string{"build", "-o", build.path}
		if build.flags != "" {
			args = append(args, "-ldflags", build.flags)
		}
		args = append(args, "../../cmd/orbit")
		if output, e := exec.Command("go", args...).CombinedOutput(); e != nil {
			t.Fatal(e, string(output))
		}
	}
	data, e := os.ReadFile(next)
	if e != nil {
		t.Fatal(e)
	}
	var archive bytes.Buffer
	if runtime.GOOS == "windows" {
		z := zip.NewWriter(&archive)
		entry, _ := z.Create("orbit.exe")
		_, _ = entry.Write(data)
		_ = z.Close()
	} else {
		gz := gzip.NewWriter(&archive)
		tarWriter := tar.NewWriter(gz)
		_ = tarWriter.WriteHeader(&tar.Header{Name: "orbit", Mode: 0755, Size: int64(len(data)), Typeflag: tar.TypeReg})
		_, _ = tarWriter.Write(data)
		_ = tarWriter.Close()
		_ = gz.Close()
	}
	compressed := archive.Bytes()
	digest := fmt.Sprintf("%x", sha256.Sum256(compressed))
	var corrupt atomic.Bool
	corrupt.Store(true)
	var downloads atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
			t.Error("update exposed credentials")
		}
		if r.URL.Path == "/terminal/manifest.json" {
			d := digest
			if corrupt.Load() {
				d = strings.Repeat("0", 64)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"version": "9.0.0", "artifacts": []any{map[string]any{"file": artifactName("9.0.0"), "available": true, "size": len(compressed), "sha256": d}}})
			return
		}
		if r.URL.Path != "/terminal/downloads/"+artifactName("9.0.0") {
			t.Error("unexpected download", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		downloads.Add(1)
		_, _ = w.Write(compressed)
	}))
	defer srv.Close()
	root, bin, config := filepath.Join(dir, "安装目录"), filepath.Join(dir, "命令目录"), filepath.Join(dir, "配置目录")
	env := append(os.Environ(), "ORBIT_CONFIG_DIR=", "ORBIT_SERVER=", "ORBIT_CA_BUNDLE=", "ORBIT_ALLOW_HTTP=")
	run := func(exe string, args ...string) (string, error) {
		command := exec.Command(exe, args...)
		command.Env = env
		output, e := command.CombinedOutput()
		return string(output), e
	}
	originalServer := "http://127.0.0.1:1"
	if output, e := run(current, "setup", "--server", originalServer, "--install-dir", root, "--bin-dir", bin, "--config-dir", config, "--json"); e != nil {
		t.Fatal(e, output)
	}
	launcher := filepath.Join(bin, executableName())
	// This opaque fixture is intentionally not valid login JSON: updating must not
	// read credentials at all, let alone rewrite or refresh them.
	credential := filepath.Join(config, "auth.json")
	if e = os.WriteFile(credential, []byte("opaque test credential bytes"), 0600); e != nil {
		t.Fatal(e)
	}
	before, _ := os.ReadFile(filepath.Join(config, "config.json"))
	alternate := filepath.Join(dir, "临时配置")
	if e = os.MkdirAll(alternate, 0700); e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(alternate, "config.json"), []byte(`{"timeout":60,"poll_seconds":2}`), 0600); e != nil {
		t.Fatal(e)
	}
	updateArgs := []string{"update", "--server", srv.URL, "--config-dir", alternate, "--json"}
	if output, e := run(launcher, updateArgs...); e == nil || !strings.Contains(output, "update_checksum_failed") {
		t.Fatal("corrupt update accepted", e, output)
	}
	if output, e := run(launcher, "--version"); e != nil || strings.TrimSpace(output) != "orbit "+Version {
		t.Fatal("original install changed", e, output)
	}
	corrupt.Store(false)
	if output, e := run(launcher, append(updateArgs, "--check")...); e != nil || !strings.Contains(output, `"status":"available"`) {
		t.Fatal(e, output)
	}
	if downloads.Load() != 1 {
		t.Fatal("check downloaded a package", downloads.Load())
	}
	releaseLock := holdUpdateTestLauncher(t, launcher)
	updateOutput, updateErr := run(launcher, updateArgs...)
	if updateErr != nil {
		t.Fatal(updateErr, updateOutput)
	}
	if runtime.GOOS == "windows" {
		var pending struct{ Data updateReport }
		if e = json.Unmarshal([]byte(updateOutput), &pending); e != nil {
			t.Fatal(e, updateOutput)
		}
		deadline := time.Now().Add(15 * time.Second)
		for {
			log, _ := os.ReadFile(pending.Data.LogFile)
			if strings.Contains(string(log), "update_busy") {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("busy setup was not retried", string(log), updateOutput)
			}
			time.Sleep(100 * time.Millisecond)
		}
		marker, e := readInstallation(filepath.Join(root, installMarker))
		if e != nil || marker.Version != Version {
			t.Fatal("busy launcher partially changed root installation", marker, e)
		}
	}
	releaseLock()
	deadline := time.Now().Add(20 * time.Second)
	for {
		output, e := run(launcher, "--version")
		if e == nil && strings.TrimSpace(output) == "orbit 9.0.0" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("installed update did not finish", e, output)
		}
		time.Sleep(100 * time.Millisecond)
	}
	after, _ := os.ReadFile(filepath.Join(config, "config.json"))
	if !bytes.Equal(before, after) {
		t.Fatal("public configuration changed during update")
	}
	output, e := run(launcher, "config", "show", "--json")
	var settingsResult struct {
		Data struct {
			Server string
			Root   string `json:"config_dir"`
		}
	}
	parseErr := json.Unmarshal([]byte(output), &settingsResult)
	wantConfig, _ := filepath.EvalSymlinks(config)
	if e != nil || parseErr != nil || settingsResult.Data.Server != originalServer || filepath.Clean(settingsResult.Data.Root) != filepath.Clean(wantConfig) {
		t.Fatal("temporary update options replaced installation defaults", e, output)
	}
	auth, _ := os.ReadFile(credential)
	if string(auth) != "opaque test credential bytes" {
		t.Fatal("credentials changed during update")
	}
	if output, e := run(launcher, "uninstall", "--yes", "--purge", "--json"); e != nil {
		t.Fatal(e, output)
	}
	deadline = time.Now().Add(20 * time.Second)
	for {
		if _, e = os.Lstat(launcher); os.IsNotExist(e) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("updated install cannot uninstall")
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func TestVersionsCompareNumericallyAndRejectInvalidReleaseNames(t *testing.T) {
	for _, tt := range []struct {
		left, right string
		want        int
	}{{"0.1.2", "0.1.10", -1}, {"1.0.0", "0.99.99", 1}, {"0.1.2", "0.1.2", 0}} {
		got, e := compareVersions(tt.left, tt.right)
		if e != nil || got != tt.want {
			t.Fatal(tt, got, e)
		}
	}
	for _, bad := range []string{"../../orbit", "0.1.2/evil", "0.01.2", "0.1.2-beta", "1.2", "999999999999999999999999.1.0"} {
		if _, e := compareVersions("0.1.2", bad); e == nil {
			t.Fatal("unsafe version accepted", bad)
		}
	}
}

func updateManifest(version string, available bool) map[string]any {
	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	return map[string]any{"version": version, "artifacts": []any{map[string]any{
		"file": "orbit-" + version + "-" + runtime.GOOS + "-" + runtime.GOARCH + ext, "available": available, "sha256": strings.Repeat("a", 64), "size": 1024,
	}}}
}

func TestLoginUpdateNoticeIsOptionalAndNeverChangesLoginData(t *testing.T) {
	for _, tt := range []struct {
		name     string
		manifest any
		status   int
		want     string
	}{
		{"new release", updateManifest("9.0.0", true), 200, "available"},
		{"same release", updateManifest(Version, true), 200, "current"},
		{"not published", updateManifest("9.0.0", false), 200, "unpublished"},
		{"old portal", nil, 404, "unavailable"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			manifestCalls := 0
			now := time.Now().Unix()
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/terminal/manifest.json" {
					manifestCalls++
					if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" {
						t.Error("credentials sent to update service")
					}
					w.WriteHeader(tt.status)
					_ = json.NewEncoder(w).Encode(tt.manifest)
					return
				}
				if r.URL.Path != prefix+"/auth/login" {
					t.Error("unexpected endpoint", r.URL.Path)
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": map[string]any{"username": "alice", "display_name": "林晨", "token": strings.Repeat("T", 43), "issued_at": now, "expires_at": now + 604800}})
			}))
			defer srv.Close()
			testArgs(t, srv.URL)
			var out, err bytes.Buffer
			if code := Run([]string{"login", "--username", "alice", "--password-stdin", "--json"}, strings.NewReader("TestOnlyPass\n"), &out, &err); code != 0 {
				t.Fatal(code, err.String())
			}
			var payload struct {
				OK     bool
				Data   map[string]any
				Update map[string]any
			}
			if e := json.Unmarshal(out.Bytes(), &payload); e != nil {
				t.Fatal(e, out.String())
			}
			if !payload.OK || payload.Data["username"] != "alice" || payload.Data["expires_at"].(float64)-payload.Data["issued_at"].(float64) != 604800 {
				t.Fatal(payload)
			}
			if payload.Update["status"] != tt.want || manifestCalls != 1 {
				t.Fatal(payload.Update, manifestCalls)
			}
			if strings.Contains(out.String()+err.String(), "TestOnlyPass") || strings.Contains(out.String(), strings.Repeat("T", 43)) {
				t.Fatal("secret leaked")
			}
		})
	}
}

func TestUpdateCheckDoesNotDownloadOrRequireLogin(t *testing.T) {
	for _, tt := range []struct {
		version string
		want    string
	}{{"0.0.1", "newer_local"}, {Version, "current"}, {"9.0.0", "available"}} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "GET" || r.URL.Path != "/terminal/manifest.json" {
				t.Error("check must only fetch public manifest", r.Method, r.URL.Path)
			}
			_ = json.NewEncoder(w).Encode(updateManifest(tt.version, true))
		}))
		testArgs(t, srv.URL)
		var out, err bytes.Buffer
		code := Run([]string{"update", "--check", "--json"}, strings.NewReader(""), &out, &err)
		srv.Close()
		if code != 0 {
			t.Fatal(code, err.String())
		}
		var payload struct{ Data map[string]any }
		if e := json.Unmarshal(out.Bytes(), &payload); e != nil {
			t.Fatal(e, out.String())
		}
		if payload.Data["status"] != tt.want {
			t.Fatal(tt.version, payload.Data)
		}
	}
}

func TestUpdateManifestRejectsRedirectAndUnsafeDownloadPath(t *testing.T) {
	seen := 0
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { seen++; http.Error(w, "unexpected", 500) }))
	defer other.Close()
	for _, tt := range []struct {
		name     string
		redirect bool
		file     string
	}{{"redirect", true, ""}, {"path", false, "../orbit"}, {"foreign URL", false, other.URL + "/orbit"}} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if tt.redirect {
				http.Redirect(w, r, other.URL, http.StatusFound)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"version": "9.0.0", "artifacts": []any{map[string]any{"file": tt.file, "available": true, "sha256": strings.Repeat("a", 64), "size": 1}}})
		}))
		testArgs(t, srv.URL)
		var out, err bytes.Buffer
		code := Run([]string{"update", "--check", "--json"}, strings.NewReader(""), &out, &err)
		srv.Close()
		if code == 0 {
			t.Fatal(tt.name, "unsafe manifest accepted", out.String())
		}
	}
	if seen != 0 {
		t.Fatal("followed foreign server redirect")
	}
}
