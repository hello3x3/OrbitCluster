package cli

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
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
