package cli

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
)

func TestWindowsFullOrReadOnlyBinDoesNotPartiallyUpdateRoot(t *testing.T) {
	dir := t.TempDir()
	root, bin := filepath.Join(dir, "root"), filepath.Join(dir, "bin")
	for _, path := range []string{root, bin} {
		if e := os.Mkdir(path, 0755); e != nil {
			t.Fatal(e)
		}
	}
	target, launcher, source := filepath.Join(root, "orbit.exe"), filepath.Join(bin, "orbit.exe"), filepath.Join(dir, "new.exe")
	for _, path := range []string{target, launcher} {
		if e := os.WriteFile(path, []byte("old program"), 0755); e != nil {
			t.Fatal(e)
		}
	}
	if e := os.WriteFile(source, []byte("new program"), 0755); e != nil {
		t.Fatal(e)
	}
	digest, _ := binaryDigest(target)
	old := installation{Schema: 1, Version: Version, Root: root, Launcher: launcher, Config: filepath.Join(dir, "config"), Digest: digest}
	marker, _ := json.Marshal(old)
	for _, path := range []string{filepath.Join(root, installMarker), launcher + ".install.json"} {
		if e := os.WriteFile(path, marker, 0600); e != nil {
			t.Fatal(e)
		}
	}
	next := old
	next.Version = "9.0.0"
	next.Digest, _ = binaryDigest(source)
	sid, e := currentSID()
	if e != nil {
		t.Fatal(e)
	}
	// Deny creating files in bin only; existing files retain their permissions.
	if output, e := exec.Command("icacls", bin, "/deny", "*"+sid+":(WD)").CombinedOutput(); e != nil {
		t.Fatal(e, string(output))
	}
	defer exec.Command("icacls", bin, "/remove:d", "*"+sid).Run()
	e = installUpdatedBinary(source, old, next)
	if e == nil || !strings.Contains(e.Error(), "暂存失败") {
		t.Fatal("did not exercise late bin staging failure", e)
	}
	for _, path := range []string{target, launcher} {
		data, _ := os.ReadFile(path)
		if string(data) != "old program" {
			t.Fatal("partial executable replacement", path, string(data))
		}
	}
	for _, path := range []string{filepath.Join(root, installMarker), launcher + ".install.json"} {
		data, _ := os.ReadFile(path)
		if string(data) != string(marker) {
			t.Fatal("partial marker replacement", path, string(data))
		}
	}
}

func holdUpdateTestLauncher(t *testing.T, path string) func() {
	t.Helper()
	name, e := syscall.UTF16PtrFromString(path)
	if e != nil {
		t.Fatal(e)
	}
	h, e := syscall.CreateFile(name, syscall.GENERIC_READ, syscall.FILE_SHARE_READ, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if e != nil {
		t.Fatal(e)
	}
	var once sync.Once
	release := func() { once.Do(func() { _ = syscall.CloseHandle(h) }) }
	t.Cleanup(release)
	return release
}
func TestWindowsUpdateGuardChecksBothExecutablesBeforeMutation(t *testing.T) {
	dir := t.TempDir()
	target, launcher := filepath.Join(dir, "orbit.exe"), filepath.Join(dir, "launcher.exe")
	for _, p := range []string{target, launcher} {
		if e := os.WriteFile(p, []byte("test executable"), 0600); e != nil {
			t.Fatal(e)
		}
	}
	closeReader := holdUpdateTestLauncher(t, launcher)
	if release, e := lockUpdateTargets(target, launcher); e == nil {
		release()
		t.Fatal("locked launcher accepted")
	}
	closeReader()
	release, e := lockUpdateTargets(target, launcher)
	if e != nil {
		t.Fatal("target handle leaked after rejected launcher", e)
	}
	defer release()
	// The Win32 POSIX rename must replace both paths while guards remain held.
	for _, p := range []string{target, launcher} {
		newPath := p + ".new"
		if e := os.WriteFile(newPath, []byte("updated executable"), 0600); e != nil {
			t.Fatal(e)
		}
		if e := renameUpdateFile(newPath, p); e != nil {
			t.Fatal("guard blocks its own atomic replacement", e)
		}
	}
}
