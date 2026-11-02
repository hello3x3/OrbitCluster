//go:build !windows

package cli

import "os/exec"

func lockUpdateTargets(paths ...string) (func(), error) { return func() {}, nil }

func applyUpdate(target string, args []string, work string) (bool, error) {
	if output, e := exec.Command(target, args...).CombinedOutput(); e != nil {
		return false, &failure{"更新安装失败：" + cleanText(string(output)), "update_install_failed", 1}
	}
	return false, nil
}
