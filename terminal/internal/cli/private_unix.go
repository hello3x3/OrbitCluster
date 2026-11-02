//go:build !windows

package cli

import (
	"os"
	"os/exec"
	"strings"
	"syscall"
)

func checkOwner(p string, st os.FileInfo) error {
	if v, ok := st.Sys().(*syscall.Stat_t); ok && int(v.Uid) != os.Getuid() {
		return configError("本机配置不属于当前用户：" + p)
	}
	return nil
}
func checkPrivate(p string, st os.FileInfo) error {
	if e := checkOwner(p, st); e != nil {
		return e
	}
	if st.Mode().Perm()&0077 != 0 {
		return configError("凭证文件权限需为 600：" + p)
	}
	return nil
}
func secureRoot(p string) error {
	if e := os.Chmod(p, 0700); e != nil {
		return configError("无法保护配置目录权限：" + p)
	}
	return nil
}
func hideInput() (func(), error) {
	c := exec.Command("stty", "-g")
	c.Stdin = os.Stdin
	b, e := c.Output()
	if e != nil {
		return nil, configError("无法隐藏密码输入；请在真实终端登录，或使用 --password-stdin")
	}
	c = exec.Command("stty", "-echo")
	c.Stdin = os.Stdin
	if c.Run() != nil {
		return nil, configError("无法隐藏密码输入")
	}
	return func() { c := exec.Command("stty", strings.TrimSpace(string(b))); c.Stdin = os.Stdin; _ = c.Run() }, nil
}

func isConsole(f *os.File) bool { c := exec.Command("stty", "-g"); c.Stdin = f; return c.Run() == nil }
