// Build release archives using Go only. End users download the archives from their portal.
package main

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"orbitcluster/terminal/internal/cli"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func main() {
	output := flag.String("output", "../oa/cluster-portal/terminal-downloads", "release directory")
	target := flag.String("target", "", "optional os/arch")
	flag.Parse()
	if e := build(*output, *target); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
func build(output, target string) error {
	if e := os.MkdirAll(output, 0755); e != nil {
		return e
	}
	artifacts := []map[string]any{}
	for _, platform := range []string{"darwin/arm64", "darwin/amd64", "linux/amd64", "linux/arm64", "windows/amd64", "windows/arm64"} {
		if target != "" && platform != target {
			continue
		}
		parts := strings.Split(platform, "/")
		name := "orbit"
		extension := ".tar.gz"
		if parts[0] == "windows" {
			name += ".exe"
			extension = ".zip"
		}
		work, e := os.MkdirTemp("", "orbit-release-")
		if e != nil {
			return e
		}
		exe := filepath.Join(work, name)
		cmd := exec.Command("go", "build", "-trimpath", "-ldflags=-s -w", "-o", exe, "./cmd/orbit")
		cmd.Env = append(os.Environ(), "GOOS="+parts[0], "GOARCH="+parts[1], "CGO_ENABLED=0")
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if e = cmd.Run(); e != nil {
			os.RemoveAll(work)
			return e
		}
		filename := "orbit-" + cli.Version + "-" + parts[0] + "-" + parts[1] + extension
		path := filepath.Join(output, filename)
		if e = archive(exe, path, name, extension == ".zip"); e != nil {
			os.RemoveAll(work)
			return e
		}
		os.RemoveAll(work)
		f, e := os.Open(path)
		if e != nil {
			return e
		}
		hash := sha256.New()
		_, e = io.Copy(hash, f)
		st, _ := f.Stat()
		f.Close()
		if e != nil {
			return e
		}
		digest := hex.EncodeToString(hash.Sum(nil))
		if e = os.WriteFile(path+".sha256", []byte(digest+"  "+filename+"\n"), 0644); e != nil {
			return e
		}
		artifacts = append(artifacts, map[string]any{"platform": parts[0], "arch": parts[1], "file": filename, "size": st.Size(), "sha256": digest})
		fmt.Println("Built", filename, st.Size())
	}
	if len(artifacts) == 0 {
		return fmt.Errorf("unsupported target: %s", target)
	}
	b, e := json.MarshalIndent(map[string]any{"version": cli.Version, "artifacts": artifacts}, "", "  ")
	if e != nil {
		return e
	}
	return os.WriteFile(filepath.Join(output, "manifest.json"), append(b, '\n'), 0644)
}
func archive(exe, path, name string, windows bool) error {
	in, e := os.Open(exe)
	if e != nil {
		return e
	}
	defer in.Close()
	out, e := os.Create(path)
	if e != nil {
		return e
	}
	defer out.Close()
	if windows {
		writer := zip.NewWriter(out)
		entry, e := writer.Create(name)
		if e == nil {
			_, e = io.Copy(entry, in)
		}
		ce := writer.Close()
		if e != nil {
			return e
		}
		return ce
	}
	gz := gzip.NewWriter(out)
	writer := tar.NewWriter(gz)
	st, _ := in.Stat()
	e = writer.WriteHeader(&tar.Header{Name: name, Mode: 0755, Size: st.Size(), Typeflag: tar.TypeReg})
	if e == nil {
		_, e = io.Copy(writer, in)
	}
	ce := writer.Close()
	ge := gz.Close()
	if e != nil {
		return e
	}
	if ce != nil {
		return ce
	}
	return ge
}
