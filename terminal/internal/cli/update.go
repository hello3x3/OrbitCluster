package cli

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const maxUpdateBytes = 64 << 20

type releaseArtifact struct {
	File      string `json:"file"`
	Available bool   `json:"available"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
}
type releaseManifest struct {
	Version   string            `json:"version"`
	Artifacts []releaseArtifact `json:"artifacts"`
}
type updateReport struct {
	Current  string           `json:"current_version"`
	Latest   string           `json:"latest_version,omitempty"`
	Status   string           `json:"status"`
	Message  string           `json:"message"`
	Command  string           `json:"command,omitempty"`
	Artifact *releaseArtifact `json:"artifact,omitempty"`
	LogFile  string           `json:"log_file,omitempty"`
}

var stableVersion = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func versionParts(version string) ([3]uint64, error) {
	parts := [3]uint64{}
	if !stableVersion.MatchString(version) {
		return parts, fmt.Errorf("invalid stable version")
	}
	for i, part := range strings.Split(version, ".") {
		n, e := strconv.ParseUint(part, 10, 64)
		if e != nil {
			return parts, e
		}
		parts[i] = n
	}
	return parts, nil
}
func compareVersions(left, right string) (int, error) {
	a, e := versionParts(left)
	if e != nil {
		return 0, e
	}
	b, e := versionParts(right)
	if e != nil {
		return 0, e
	}
	for i := range a {
		if a[i] < b[i] {
			return -1, nil
		}
		if a[i] > b[i] {
			return 1, nil
		}
	}
	return 0, nil
}
func artifactName(version string) string {
	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	return "orbit-" + version + "-" + runtime.GOOS + "-" + runtime.GOARCH + ext
}

// Distribution requests never carry a token or a Cookie, and never follow a redirect.
func publicGET(c *client, path string, timeout time.Duration) (*http.Response, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	req, e := http.NewRequestWithContext(ctx, "GET", c.s.Server+path, nil)
	if e != nil {
		cancel()
		return nil, e
	}
	response, e := c.http.Do(req)
	if e != nil {
		cancel()
		return nil, e
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		cancel()
		return nil, fmt.Errorf("HTTP %d", response.StatusCode)
	}
	response.Body = &cancelBody{ReadCloser: response.Body, cancel: cancel}
	return response, nil
}

type cancelBody struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (b *cancelBody) Close() error { e := b.ReadCloser.Close(); b.cancel(); return e }

func checkUpdate(s settings) updateReport {
	report := updateReport{Current: Version, Status: "unavailable", Message: "暂时无法检查更新，可稍后运行 orbit update --check。"}
	c, e := newClient(s)
	if e != nil {
		return report
	}
	defer c.http.CloseIdleConnections()
	response, e := publicGET(c, "/terminal/manifest.json", 3*time.Second)
	if e != nil {
		return report
	}
	defer response.Body.Close()
	var manifest releaseManifest
	if json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&manifest) != nil {
		return report
	}
	comparison, e := compareVersions(Version, manifest.Version)
	if e != nil {
		return report
	}
	report.Latest = manifest.Version
	if comparison > 0 {
		report.Status = "newer_local"
		report.Message = "本机版本高于服务器发布版本，保留本机版本。"
		return report
	}
	if comparison == 0 {
		report.Status = "current"
		report.Message = "当前 CLI 已是服务器发布的最新版本。"
		return report
	}
	name := artifactName(manifest.Version)
	for _, artifact := range manifest.Artifacts {
		if artifact.File != name {
			continue
		}
		if !artifact.Available {
			report.Status = "unpublished"
			report.Message = "有新版本，但当前平台安装包尚未发布。"
			return report
		}
		digest, e := hex.DecodeString(artifact.SHA256)
		if e != nil || len(digest) != 32 || artifact.Size <= 0 || artifact.Size > maxUpdateBytes {
			return report
		}
		report.Status = "available"
		report.Message = "发现可用更新。"
		report.Command = "orbit update"
		report.Artifact = &artifact
		return report
	}
	return report
}
func writeUpdateNotice(out io.Writer, report updateReport) {
	if report.Status == "available" {
		fmt.Fprintf(out, "\n发现新版 CLI：%s → %s。运行 orbit update 更新，保留配置和登录。\n", report.Current, report.Latest)
		return
	}
	fmt.Fprintln(out, "\n更新检查："+cleanText(report.Message))
}

func updateCLI(a arguments, s settings, progress io.Writer) (any, error) {
	if a.Bool["json"] {
		progress = io.Discard
	}
	report := checkUpdate(s)
	if report.Status == "unavailable" {
		return nil, &failure{"无法获取有效更新清单，请确认门户已发布客户端，稍后运行 orbit update --check 重试", "update_unavailable", 4}
	}
	if a.Bool["check"] || report.Status != "available" {
		return report, nil
	}
	exe, e := os.Executable()
	if e != nil {
		return nil, configError("无法定位当前客户端")
	}
	m, e := findInstallation(exe)
	if e != nil {
		return nil, e
	}
	if m == nil {
		return nil, configError("当前是便携或开发版本；请用门户安装器安装后运行 orbit update")
	}
	work, e := os.MkdirTemp("", "orbit-update-")
	if e != nil {
		return nil, configError("无法创建更新临时目录")
	}
	cleanup := true
	defer func() {
		if cleanup {
			os.RemoveAll(work)
		}
	}()
	c, e := newClient(s)
	if e != nil {
		return nil, e
	}
	defer c.http.CloseIdleConnections()
	artifact := report.Artifact
	fmt.Fprintf(progress, "[1/3] 从门户下载 CLI %s (%s/%s)\n", report.Latest, runtime.GOOS, runtime.GOARCH)
	response, e := publicGET(c, "/terminal/downloads/"+artifact.File, 5*time.Minute)
	if e != nil {
		return nil, &failure{"更新下载失败，原安装保持不变", "update_download_failed", 4}
	}
	archive := filepath.Join(work, "client.archive")
	f, e := os.Create(archive)
	if e != nil {
		response.Body.Close()
		return nil, configError("无法保存更新包")
	}
	hash := sha256.New()
	count, copyErr := io.Copy(io.MultiWriter(f, hash), io.LimitReader(response.Body, maxUpdateBytes+1))
	closeErr := f.Close()
	response.Body.Close()
	if copyErr != nil || closeErr != nil || count != artifact.Size {
		return nil, &failure{"更新包不完整或大小不匹配，原安装保持不变", "update_download_failed", 4}
	}
	fmt.Fprintln(progress, "[2/3] 校验 SHA256、归档和客户端版本")
	if !strings.EqualFold(hex.EncodeToString(hash.Sum(nil)), artifact.SHA256) {
		return nil, &failure{"更新包 SHA256 校验失败，原安装保持不变", "update_checksum_failed", 1}
	}
	target := filepath.Join(work, executableName())
	if e = extractUpdate(archive, target); e != nil {
		return nil, e
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	version, e := exec.CommandContext(ctx, target, "--version").Output()
	if e != nil || strings.TrimSpace(string(version)) != "orbit "+report.Latest {
		return nil, &failure{"更新包中的客户端版本不匹配，原安装保持不变", "update_invalid_archive", 1}
	}
	fmt.Fprintln(progress, "[3/3] 更新程序，保留配置和登录")
	args := []string{"setup", "--server", s.Server, "--install-dir", m.Root, "--bin-dir", filepath.Dir(m.Launcher), "--config-dir", s.Root, "--preserve-config", "--json"}
	if s.CA != "" {
		args = append(args, "--ca-bundle", s.CA)
	}
	if !s.AllowHTTP {
		args = append(args, "--require-https")
	}
	scheduled, e := applyUpdate(target, args, work)
	if e != nil {
		return nil, e
	}
	if scheduled {
		cleanup = false
		report.Status = "scheduled"
		report.Message = "更新已安排，当前命令退出后替换程序；请稍后运行 orbit --version 确认。"
		report.LogFile = filepath.Join(work, "update.log")
	} else {
		report.Status = "updated"
		report.Message = "CLI 已更新，配置和登录信息已保留。"
	}
	return report, nil
}

func extractUpdate(archive, target string) error {
	invalid := func() error {
		return &failure{"更新归档不正确，原安装保持不变", "update_invalid_archive", 1}
	}
	f, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0755)
	if e != nil {
		return configError("无法解压更新程序")
	}
	defer f.Close()
	copyEntry := func(reader io.Reader, size int64) error {
		if size <= 0 || size > maxUpdateBytes {
			return invalid()
		}
		count, e := io.Copy(f, io.LimitReader(reader, maxUpdateBytes+1))
		if e != nil || count != size {
			return invalid()
		}
		return nil
	}
	if runtime.GOOS == "windows" {
		z, e := zip.OpenReader(archive)
		if e != nil {
			return invalid()
		}
		defer z.Close()
		if len(z.File) != 1 || z.File[0].Name != executableName() || !z.File[0].Mode().IsRegular() {
			return invalid()
		}
		r, e := z.File[0].Open()
		if e != nil {
			return invalid()
		}
		defer r.Close()
		return copyEntry(r, int64(z.File[0].UncompressedSize64))
	}
	input, e := os.Open(archive)
	if e != nil {
		return invalid()
	}
	defer input.Close()
	gz, e := gzip.NewReader(input)
	if e != nil {
		return invalid()
	}
	defer gz.Close()
	tarReader := tar.NewReader(gz)
	header, e := tarReader.Next()
	if e != nil || header.Name != executableName() || header.Typeflag != tar.TypeReg {
		return invalid()
	}
	if e = copyEntry(tarReader, header.Size); e != nil {
		return e
	}
	if _, e = tarReader.Next(); e != io.EOF {
		return invalid()
	}
	return nil
}
