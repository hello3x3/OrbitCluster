package cli

import (
	"os"
	"os/user"
	"runtime"
	"syscall"
	"unsafe"
)

var securityDLL = syscall.NewLazyDLL("advapi32.dll")
var kernelDLL = syscall.NewLazyDLL("kernel32.dll")

func currentSID() (string, error) {
	u, e := user.Current()
	if e != nil {
		return "", configError("无法确定当前 Windows 用户")
	}
	return u.Uid, nil
}
func fileSecurity(p string) ([]byte, error) {
	name, e := syscall.UTF16PtrFromString(p)
	if e != nil {
		return nil, configError("Windows 路径格式无效")
	}
	get := securityDLL.NewProc("GetFileSecurityW")
	var needed uint32
	get.Call(uintptr(unsafe.Pointer(name)), 5, 0, 0, uintptr(unsafe.Pointer(&needed)))
	if needed == 0 || needed > 1<<20 {
		return nil, configError("无法读取 Windows 文件访问权限：" + p)
	}
	b := make([]byte, needed)
	r, _, _ := get.Call(uintptr(unsafe.Pointer(name)), 5, uintptr(unsafe.Pointer(&b[0])), uintptr(needed), uintptr(unsafe.Pointer(&needed)))
	if r == 0 {
		return nil, configError("无法读取 Windows 文件访问权限：" + p)
	}
	return b, nil
}
func sidString(sid unsafe.Pointer) (string, error) {
	var text *uint16
	r, _, _ := securityDLL.NewProc("ConvertSidToStringSidW").Call(uintptr(sid), uintptr(unsafe.Pointer(&text)))
	if r == 0 {
		return "", configError("Windows 身份信息无效")
	}
	defer kernelDLL.NewProc("LocalFree").Call(uintptr(unsafe.Pointer(text)))
	out := []uint16{}
	for i := 0; i < 256; i++ {
		v := *(*uint16)(unsafe.Add(unsafe.Pointer(text), i*2))
		if v == 0 {
			break
		}
		out = append(out, v)
	}
	return syscall.UTF16ToString(out), nil
}
func ownerSID(b []byte) (string, error) {
	var owner unsafe.Pointer
	var defaulted uint32
	r, _, _ := securityDLL.NewProc("GetSecurityDescriptorOwner").Call(uintptr(unsafe.Pointer(&b[0])), uintptr(unsafe.Pointer(&owner)), uintptr(unsafe.Pointer(&defaulted)))
	if r == 0 || owner == nil {
		return "", configError("Windows 文件所有者无效")
	}
	s, e := sidString(owner)
	runtime.KeepAlive(b)
	return s, e
}
func trustedSID(s, current string) bool {
	return s == current || s == "S-1-5-18" || s == "S-1-5-32-544"
}
func checkOwner(p string, st os.FileInfo) error {
	b, e := fileSecurity(p)
	if e != nil {
		return e
	}
	owner, e := ownerSID(b)
	if e != nil {
		return e
	}
	current, e := currentSID()
	if e != nil {
		return e
	}
	if !trustedSID(owner, current) {
		return configError("Windows 文件不属于当前用户：" + p)
	}
	return nil
}
func checkPrivate(p string, st os.FileInfo) error {
	b, e := fileSecurity(p)
	if e != nil {
		return e
	}
	current, e := currentSID()
	if e != nil {
		return e
	}
	owner, e := ownerSID(b)
	if e != nil {
		return e
	}
	if !trustedSID(owner, current) {
		return configError("凭证文件不属于当前 Windows 用户")
	}
	var acl unsafe.Pointer
	var present, defaulted uint32
	r, _, _ := securityDLL.NewProc("GetSecurityDescriptorDacl").Call(uintptr(unsafe.Pointer(&b[0])), uintptr(unsafe.Pointer(&present)), uintptr(unsafe.Pointer(&acl)), uintptr(unsafe.Pointer(&defaulted)))
	if r == 0 || present == 0 || acl == nil {
		return configError("Windows 凭证文件权限不安全")
	}
	count := *(*uint16)(unsafe.Add(acl, 4))
	for i := 0; i < int(count); i++ {
		var ace unsafe.Pointer
		r, _, _ := securityDLL.NewProc("GetAce").Call(uintptr(acl), uintptr(i), uintptr(unsafe.Pointer(&ace)))
		if r == 0 || ace == nil {
			return configError("无法校验 Windows 凭证权限")
		}
		kind := *(*byte)(ace)
		if kind == 1 {
			continue
		}
		if kind != 0 {
			return configError("Windows 凭证具有无法验证的访问权限")
		}
		sid, e := sidString(unsafe.Add(ace, 8))
		if e != nil {
			return e
		}
		if !trustedSID(sid, current) {
			return configError("Windows 凭证文件可被其它用户访问，请修正权限后重新登录")
		}
	}
	runtime.KeepAlive(b)
	return nil
}
func secureRoot(p string) error {
	sid, e := currentSID()
	if e != nil {
		return e
	}
	sddl, e := syscall.UTF16PtrFromString("O:" + sid + "D:P(A;OICI;FA;;;" + sid + ")")
	if e != nil {
		return configError("Windows 权限配置无效")
	}
	var descriptor unsafe.Pointer
	r, _, _ := securityDLL.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW").Call(uintptr(unsafe.Pointer(sddl)), 1, uintptr(unsafe.Pointer(&descriptor)), 0)
	if r == 0 {
		return configError("无法建立 Windows 私有访问权限")
	}
	defer kernelDLL.NewProc("LocalFree").Call(uintptr(descriptor))
	path, e := syscall.UTF16PtrFromString(p)
	if e != nil {
		return configError("Windows 路径无效")
	}
	r, _, _ = securityDLL.NewProc("SetFileSecurityW").Call(uintptr(unsafe.Pointer(path)), uintptr(0x80000005), uintptr(descriptor))
	if r == 0 {
		return configError("无法保护 Windows 配置目录：" + p)
	}
	return nil
}
func hideInput() (func(), error) {
	get := kernelDLL.NewProc("GetConsoleMode")
	set := kernelDLL.NewProc("SetConsoleMode")
	var mode uint32
	h := os.Stdin.Fd()
	r, _, _ := get.Call(h, uintptr(unsafe.Pointer(&mode)))
	if r == 0 {
		return nil, configError("请在 Windows Terminal 登录，或使用 --password-stdin")
	}
	r, _, _ = set.Call(h, uintptr(mode&^4))
	if r == 0 {
		return nil, configError("无法隐藏密码输入")
	}
	return func() { set.Call(h, uintptr(mode)) }, nil
}
func isConsole(f *os.File) bool {
	get := kernelDLL.NewProc("GetConsoleMode")
	var mode uint32
	r, _, _ := get.Call(f.Fd(), uintptr(unsafe.Pointer(&mode)))
	return r != 0
}
