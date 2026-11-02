package cli

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"time"
)

const Version = "0.2.0"

type arguments struct {
	Pos  []string
	Val  map[string]string
	Bool map[string]bool
}

func parse(args []string) (arguments, error) {
	a := arguments{Val: map[string]string{}, Bool: map[string]bool{}}
	boolean := strings.Fields("help version json yes password-stdin local force allow-http require-https purge")
	valued := strings.Fields("server ca-bundle config-dir request-timeout poll-seconds username device name instance plan image hours node account lines until timeout file install-dir bin-dir")
	for i := 0; i < len(args); i++ {
		v := args[i]
		if v == "--" {
			a.Pos = append(a.Pos, args[i+1:]...)
			break
		}
		if v == "-h" {
			v = "--help"
		}
		if !strings.HasPrefix(v, "--") {
			if strings.HasPrefix(v, "-") {
				return a, configError("未知参数：" + v)
			}
			a.Pos = append(a.Pos, v)
			continue
		}
		key, value, has := strings.Cut(v[2:], "=")
		found := false
		for _, b := range boolean {
			if key == b {
				if has {
					p, e := asBool(value)
					if e != nil {
						return a, e
					}
					a.Bool[key] = p
				} else {
					a.Bool[key] = true
				}
				found = true
				break
			}
		}
		if found {
			continue
		}
		for _, k := range valued {
			if key == k {
				if !has {
					i++
					if i >= len(args) || strings.HasPrefix(args[i], "--") {
						return a, configError("缺少参数：--" + key)
					}
					value = args[i]
				}
				a.Val[key] = value
				found = true
				break
			}
		}
		if !found {
			return a, configError("未知参数：--" + key)
		}
	}
	return a, nil
}
func help(pos []string) string {
	base := `OrbitCluster CLI ` + Version + ` — 用户与 agent 的集群命令工具
用法：orbit [全局选项] <命令> [参数]

  login --username 用户名           隐藏输入密码，保存服务器签发的七天凭证
  whoami                            查看身份和到期时间
  logout [--local]                  撤销当前登录（local 仅清理本机）
  plans | nodes | quota | accounts   查询可用资源与额度
  instances list                    查询我的资源
  instances create --name NAME --plan ID --image NAME --hours N [--account NAME] [--node NAME]
  instances show|logs|connection|stop|restart|delete ID
  instances save ID --name NAME [--force]
  instances wait ID --until ssh-ready|saved|terminal [--timeout 300]
  images list | images save --instance ID --name NAME
  images rename NAME NEW_NAME | images comment NAME NOTE | images delete NAME
  profile show | profile name NAME | profile password
  keys list | keys add --file PUBLIC_KEY.pub | keys delete ID
  config show | config set server|ca_bundle|timeout|poll_seconds|allow_http VALUE
  uninstall [--purge] [--yes]        卸载客户端；purge 同时清理本机登录和配置

全局参数可放在子命令后：--json --yes --server URL --config-dir DIR
  --ca-bundle FILE --request-timeout 120 --poll-seconds 5 --require-https
HTTP 默认可用；HTTPS 正常校验证书。无 Cookie、无自动续期、写请求不自动重试。
JSON 成功写 stdout，失败写 stderr。退出码：0成功 1业务失败 2参数/配置 3需登录 4网络故障 130取消。
`
	return base
}
func Run(args []string, in io.Reader, out, errOut io.Writer) int {
	a, e := parse(args)
	if e == nil && a.Bool["version"] {
		fmt.Fprintln(out, "orbit "+Version)
		return 0
	}
	if e == nil && (a.Bool["help"] || len(a.Pos) == 0) {
		fmt.Fprint(out, help(a.Pos))
		return 0
	}
	var data any
	if e == nil {
		data, e = execute(a, in, errOut)
	}
	if e != nil {
		f, ok := e.(*failure)
		if !ok {
			f = &failure{"本机操作失败，请检查文件权限或重新安装客户端", "local_error", 2}
		}
		if a.Bool["json"] {
			_ = json.NewEncoder(errOut).Encode(map[string]any{"ok": false, "code": f.Code, "error": f.Message})
		} else {
			fmt.Fprintln(errOut, f.Message)
		}
		return f.Exit
	}
	if a.Bool["json"] {
		_ = json.NewEncoder(out).Encode(map[string]any{"ok": true, "data": data})
	} else {
		enc := json.NewEncoder(out)
		enc.SetIndent("", "  ")
		if d, ok := data.(map[string]any); ok {
			if n, ok := d["expires_at"].(float64); ok {
				d["expires_at_local"] = time.Unix(int64(n), 0).Local().Format(time.RFC3339)
			}
		}
		_ = enc.Encode(data)
	}
	return 0
}
func required(a arguments, keys ...string) error {
	for _, k := range keys {
		if strings.TrimSpace(a.Val[k]) == "" {
			return configError("请指定 --" + k)
		}
	}
	return nil
}
func intValue(v string) (string, error) {
	n, e := strconv.ParseInt(v, 10, 64)
	if e != nil || n <= 0 {
		return "", configError("ID、时长和行数需为正整数")
	}
	return strconv.FormatInt(n, 10), nil
}
func requirePositions(a arguments, n int) error {
	if len(a.Pos) != n {
		return configError("命令参数数量不正确，请运行 orbit --help")
	}
	return nil
}
func confirmed(a arguments, in io.Reader, errOut io.Writer, description string) error {
	if a.Bool["yes"] {
		return nil
	}
	if a.Bool["json"] || !terminalInput(in) {
		return configError(description + "；请在获得用户授权后添加 --yes")
	}
	fmt.Fprint(errOut, description+"，确认？[y/N] ")
	v, _ := bufio.NewReader(in).ReadString('\n')
	if strings.ToLower(strings.TrimSpace(v)) != "y" {
		return configError("操作已取消")
	}
	return nil
}
func terminalInput(in io.Reader) bool {
	f, ok := in.(*os.File)
	if !ok {
		return false
	}
	st, e := f.Stat()
	return e == nil && st.Mode()&os.ModeCharDevice != 0 && isConsole(f)
}
func passwordInput(in io.Reader, errOut io.Writer, stdin bool) (string, error) {
	if stdin {
		b, e := io.ReadAll(io.LimitReader(in, 4097))
		if e != nil {
			return "", configError("无法读取密码")
		}
		p := strings.TrimRight(string(b), "\r\n")
		if p == "" || len(p) > 1024 || strings.ContainsAny(p, "\r\n") {
			return "", configError("请提供一行密码")
		}
		return p, nil
	}
	if !terminalInput(in) {
		return "", configError("登录请在真实终端隐藏输入密码；自动化输入可用 --password-stdin")
	}
	restore, e := hideInput()
	if e != nil {
		return "", e
	}
	var once sync.Once
	interrupts := make(chan os.Signal, 1)
	done := make(chan struct{})
	signal.Notify(interrupts, os.Interrupt)
	cleanup := func() { once.Do(func() { restore(); signal.Stop(interrupts); close(done) }) }
	defer cleanup()
	go func() {
		select {
		case <-interrupts:
			cleanup()
			os.Exit(130)
		case <-done:
		}
	}()
	fmt.Fprint(errOut, "密码：")
	p, e := bufio.NewReader(in).ReadString('\n')
	fmt.Fprintln(errOut)
	if e != nil {
		return "", configError("读取密码已取消")
	}
	p = strings.TrimRight(p, "\r\n")
	if p == "" || len(p) > 1024 {
		return "", configError("请输入密码")
	}
	return p, nil
}
func execute(a arguments, in io.Reader, errOut io.Writer) (any, error) {
	command := a.Pos[0]
	if command == "tui" {
		return nil, configError("已移除 TUI。请用 orbit --help 查看 CLI；agent 查询示例：orbit instances list --json")
	}
	if e := validateArguments(a); e != nil {
		return nil, e
	}
	if command == "setup" {
		return setup(a)
	}
	if command == "uninstall" {
		if e := confirmed(a, in, errOut, "卸载本机 OrbitCluster 客户端"); e != nil {
			return nil, e
		}
		return uninstall(a)
	}
	s, e := loadSettings(a)
	if e != nil {
		return nil, e
	}
	if command == "config" {
		if len(a.Pos) == 2 && a.Pos[1] == "show" {
			return s.public(), nil
		}
		if len(a.Pos) == 4 && a.Pos[1] == "set" {
			if e = s.save(a.Pos[2], a.Pos[3]); e != nil {
				return nil, e
			}
			s, e = loadSettings(a)
			if e != nil {
				return nil, e
			}
			return s.public(), nil
		}
		return nil, configError("用法：orbit config show 或 orbit config set KEY VALUE")
	}
	known := map[string]bool{"login": true, "logout": true, "whoami": true, "plans": true, "nodes": true, "quota": true, "accounts": true, "instances": true, "images": true, "profile": true, "keys": true}
	if !known[command] {
		return nil, configError("未知命令，请运行 orbit --help")
	}
	c, e := newClient(s)
	if e != nil {
		return nil, e
	}
	defer c.http.CloseIdleConnections()
	request := func(method, path string, v url.Values) (any, error) { return c.request(method, path, v, nil, true) }
	if command == "login" {
		if e = required(a, "username"); e != nil {
			return nil, e
		}
		p, e := passwordInput(in, errOut, a.Bool["password-stdin"])
		if e != nil {
			return nil, e
		}
		return c.login(a.Val["username"], p, a.Val["device"])
	}
	if command == "logout" {
		if !a.Bool["local"] {
			_, e = request("POST", "/auth/logout", nil)
			if f, ok := e.(*failure); e != nil && (!ok || f.Exit != 3) {
				return nil, e
			}
		}
		if e = c.credential(nil, true); e != nil {
			return nil, e
		}
		return map[string]any{"logged_out": true, "local_only": a.Bool["local"]}, nil
	}
	if command == "whoami" {
		if e = requirePositions(a, 1); e != nil {
			return nil, e
		}
		return request("GET", "/auth/me", nil)
	}
	if command == "plans" || command == "nodes" || command == "quota" || command == "accounts" {
		if e = requirePositions(a, 1); e != nil {
			return nil, e
		}
		return request("GET", "/"+command, nil)
	}
	if len(a.Pos) < 2 {
		return nil, configError("缺少子命令，请运行 orbit --help")
	}
	action := a.Pos[1]
	if command == "instances" {
		if action == "list" {
			if e = requirePositions(a, 2); e != nil {
				return nil, e
			}
			return request("GET", "/instances", nil)
		}
		if action == "create" {
			if e = required(a, "name", "plan", "image", "hours"); e != nil {
				return nil, e
			}
			hours, e := intValue(a.Val["hours"])
			if e != nil {
				return nil, e
			}
			plan, e := c.resolvePlan(a.Val["plan"])
			if e != nil {
				return nil, e
			}
			image, e := c.resolveImage(a.Val["image"])
			if e != nil {
				return nil, e
			}
			return request("POST", "/instances", url.Values{"task_name": {a.Val["name"]}, "plan_id": {plan}, "image": {image}, "hours": {hours}, "node": {a.Val["node"]}, "account": {a.Val["account"]}})
		}
		if e = requirePositions(a, 3); e != nil {
			return nil, e
		}
		id, e := intValue(a.Pos[2])
		if e != nil {
			return nil, e
		}
		base := "/instances/" + id
		switch action {
		case "show":
			return request("GET", base, nil)
		case "logs":
			lines := a.Val["lines"]
			if lines == "" {
				lines = "300"
			}
			if lines, e = intValue(lines); e != nil {
				return nil, e
			}
			return request("GET", base+"/log?lines="+lines, nil)
		case "connection":
			return request("GET", base+"/connection", nil)
		case "stop", "delete", "restart":
			if action != "restart" {
				if e = confirmed(a, in, errOut, action+" 资源 #"+id); e != nil {
					return nil, e
				}
			}
			return request("POST", base+"/"+action, nil)
		case "save":
			return saveImage(c, a, in, errOut, id)
		case "wait":
			return c.wait(a, id)
		}
	}
	if command == "images" {
		if action == "list" {
			if e = requirePositions(a, 2); e != nil {
				return nil, e
			}
			return request("GET", "/images", nil)
		}
		if action == "save" {
			if e = required(a, "instance"); e != nil {
				return nil, e
			}
			id, e := intValue(a.Val["instance"])
			if e != nil {
				return nil, e
			}
			return saveImage(c, a, in, errOut, id)
		}
		if len(a.Pos) < 3 {
			return nil, configError("请提供镜像名称")
		}
		name, e := c.resolveOwnImage(a.Pos[2])
		if e != nil {
			return nil, e
		}
		parts := strings.Split(strings.TrimSuffix(name, ".sqsh"), "/")
		name = parts[len(parts)-1]
		path := "/images/" + url.PathEscape(name) + "/" + action
		if action == "delete" {
			if e = requirePositions(a, 3); e != nil {
				return nil, e
			}
			if e = confirmed(a, in, errOut, "删除个人镜像 "+name); e != nil {
				return nil, e
			}
			return request("POST", path, nil)
		}
		if (action == "rename" || action == "comment") && len(a.Pos) == 4 {
			k := "new_name"
			if action == "comment" {
				k = "note"
			}
			return request("POST", path, url.Values{k: {a.Pos[3]}})
		}
	}
	if command == "profile" {
		switch action {
		case "show":
			return request("GET", "/profile", nil)
		case "name":
			if e = requirePositions(a, 3); e != nil {
				return nil, e
			}
			return request("POST", "/profile/name", url.Values{"display_name": {a.Pos[2]}})
		case "password":
			fmt.Fprintln(errOut, "当前密码")
			old, e := passwordInput(in, errOut, false)
			if e != nil {
				return nil, e
			}
			fmt.Fprintln(errOut, "新密码")
			next, e := passwordInput(in, errOut, false)
			if e != nil {
				return nil, e
			}
			fmt.Fprintln(errOut, "再次输入新密码")
			repeat, e := passwordInput(in, errOut, false)
			if e != nil {
				return nil, e
			}
			d, e := request("POST", "/profile/password", url.Values{"old": {old}, "new": {next}, "new2": {repeat}})
			if e == nil {
				_ = c.credential(nil, true)
			}
			return d, e
		}
	}
	if command == "keys" {
		switch action {
		case "list":
			d, e := request("GET", "/profile", nil)
			if e != nil {
				return nil, e
			}
			m, ok := d.(map[string]any)
			if !ok {
				return nil, configError("服务器返回的资料格式错误")
			}
			return m["keys"], nil
		case "add":
			if e = required(a, "file"); e != nil {
				return nil, e
			}
			b, e := os.ReadFile(a.Val["file"])
			if e != nil {
				return nil, configError("无法读取 SSH 公钥文件")
			}
			return request("POST", "/profile/keys", url.Values{"pubkey": {strings.TrimSpace(string(b))}})
		case "delete":
			if e = requirePositions(a, 3); e != nil {
				return nil, e
			}
			id, e := intValue(a.Pos[2])
			if e != nil {
				return nil, e
			}
			if e = confirmed(a, in, errOut, "删除 SSH 公钥 #"+id); e != nil {
				return nil, e
			}
			return request("POST", "/profile/keys/"+id+"/delete", nil)
		}
	}
	return nil, configError("未知子命令或参数不正确，请运行 orbit --help")
}
func saveImage(c *client, a arguments, in io.Reader, errOut io.Writer, id string) (any, error) {
	if e := required(a, "name"); e != nil {
		return nil, e
	}
	force := "0"
	if a.Bool["force"] {
		if e := confirmed(a, in, errOut, "覆盖同名个人镜像"); e != nil {
			return nil, e
		}
		force = "1"
	}
	return c.request("POST", "/instances/"+id+"/save-image", url.Values{"name": {a.Val["name"]}, "force": {force}}, nil, true)
}
func (c *client) resolvePlan(value string) (string, error) {
	d, e := c.request("GET", "/plans", nil, nil, true)
	if e != nil {
		return "", e
	}
	list, ok := d.([]any)
	if !ok {
		return "", configError("套餐数据格式错误")
	}
	found := []string{}
	for _, row := range list {
		p, ok := row.(map[string]any)
		if ok && (fmt.Sprint(p["id"]) == value || p["name"] == value) {
			found = append(found, fmt.Sprint(p["id"]))
		}
	}
	if len(found) != 1 {
		return "", configError("套餐不存在或名称不唯一，请用 orbit plans 查询")
	}
	return found[0], nil
}
func (c *client) resolveImage(value string) (string, error) {
	d, e := c.request("GET", "/images", nil, nil, true)
	if e != nil {
		return "", e
	}
	m, ok := d.(map[string]any)
	if !ok {
		return "", configError("镜像数据格式错误")
	}
	found := []string{}
	for _, key := range []string{"public", "mine"} {
		list, _ := m[key].([]any)
		for _, row := range list {
			p, ok := row.(map[string]any)
			if !ok {
				continue
			}
			path, _ := p["path"].(string)
			name, _ := p["name"].(string)
			if path == value {
				return path, nil
			}
			if name == value || strings.TrimSuffix(name, ".sqsh") == value {
				found = append(found, path)
			}
		}
	}
	if len(found) != 1 {
		return "", configError("镜像不存在或名称不唯一，请用 orbit images list 查询并指定完整路径")
	}
	return found[0], nil
}
func (c *client) wait(a arguments, id string) (any, error) {
	until := a.Val["until"]
	if until == "" {
		until = "ssh-ready"
	}
	if until != "ssh-ready" && until != "saved" && until != "terminal" {
		return nil, configError("until 只能为 ssh-ready、saved 或 terminal")
	}
	timeout := 300.0
	var e error
	if a.Val["timeout"] != "" {
		timeout, e = strconv.ParseFloat(a.Val["timeout"], 64)
		if e != nil || timeout <= 0 {
			return nil, configError("等待超时必须大于 0")
		}
	}
	deadline := time.Now().Add(time.Duration(timeout * float64(time.Second)))
	for {
		path := "/instances/" + id
		if until == "ssh-ready" {
			path += "/connection"
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return nil, &failure{"等待超时；作业继续由服务器管理", "wait_timeout", 1}
		}
		oldTimeout := c.http.Timeout
		if oldTimeout > remaining {
			c.http.Timeout = remaining
		}
		d, e := c.request("GET", path, nil, nil, true)
		c.http.Timeout = oldTimeout
		if !time.Now().Before(deadline) {
			return nil, &failure{"等待超时；作业继续由服务器管理", "wait_timeout", 1}
		}
		if e != nil {
			return nil, e
		}
		m, ok := d.(map[string]any)
		if !ok {
			return nil, configError("服务器资源数据格式错误")
		}
		state, _ := m["state"].(string)
		active := strings.Contains("|PENDING|RUNNING|SUSPENDED|COMPLETING|STOPPING|", "|"+state+"|") && state != ""
		if until == "ssh-ready" && m["ready"] == true {
			return d, nil
		}
		if until == "terminal" && !active {
			return d, nil
		}
		if until == "saved" && m["saving_now"] != true && m["last_save"] != nil && m["last_save"] != "" {
			v := fmt.Sprint(m["last_save"])
			if strings.Contains(v, "失败") || strings.Contains(v, "异常") {
				return nil, &failure{v, "operation_rejected", 1}
			}
			return d, nil
		}
		if until == "ssh-ready" && (!active || m["error"] != nil && m["error"] != "") {
			return nil, &failure{"作业已结束或 SSH 启动失败，请查询日志", "operation_rejected", 1}
		}
		remaining = time.Until(deadline)
		if remaining <= 0 {
			return nil, &failure{"等待超时；作业继续由服务器管理", "wait_timeout", 1}
		}
		pause := time.Duration(c.s.Poll * float64(time.Second))
		if pause > remaining {
			pause = remaining
		}
		time.Sleep(pause)
	}
}

func validateArguments(a arguments) error {
	pos := a.Pos
	key := pos[0]
	if len(pos) > 1 && (key == "instances" || key == "images" || key == "profile" || key == "keys" || key == "config") {
		key += " " + pos[1]
	}
	counts := map[string]int{"login": 1, "logout": 1, "whoami": 1, "plans": 1, "nodes": 1, "quota": 1, "accounts": 1, "setup": 1, "uninstall": 1, "config show": 2, "config set": 4, "instances list": 2, "instances create": 2, "images list": 2, "images save": 2, "images rename": 4, "images comment": 4, "images delete": 3, "profile show": 2, "profile name": 3, "profile password": 2, "keys list": 2, "keys add": 2, "keys delete": 3}
	for _, k := range []string{"show", "logs", "connection", "stop", "restart", "delete", "save", "wait"} {
		counts["instances "+k] = 3
	}
	if n, ok := counts[key]; !ok || len(pos) != n {
		return configError("命令参数数量不正确或子命令不存在，请运行 orbit --help")
	}
	common := "server ca-bundle config-dir request-timeout poll-seconds json yes allow-http require-https help version"
	extra := map[string]string{"login": "username device password-stdin", "logout": "local", "setup": "install-dir bin-dir", "uninstall": "purge", "instances create": "name plan image hours node account", "instances logs": "lines", "instances save": "name force", "instances wait": "until timeout", "images save": "instance name force", "keys add": "file"}
	allowed := map[string]bool{}
	for _, k := range strings.Fields(common + " " + extra[key]) {
		allowed[k] = true
	}
	for k := range a.Val {
		if !allowed[k] {
			return configError("当前命令不接受 --" + k)
		}
	}
	for k := range a.Bool {
		if !allowed[k] {
			return configError("当前命令不接受 --" + k)
		}
	}
	return nil
}

func (c *client) resolveOwnImage(value string) (string, error) {
	d, e := c.request("GET", "/images", nil, nil, true)
	if e != nil {
		return "", e
	}
	m, ok := d.(map[string]any)
	if !ok {
		return "", configError("镜像数据格式错误")
	}
	list, _ := m["mine"].([]any)
	found := []string{}
	for _, row := range list {
		p, ok := row.(map[string]any)
		if !ok {
			continue
		}
		path, _ := p["path"].(string)
		name, _ := p["name"].(string)
		if path == value || name == value || strings.TrimSuffix(name, ".sqsh") == value {
			found = append(found, path)
		}
	}
	if len(found) != 1 {
		return "", configError("只能修改自己的个人镜像；请用 orbit images list 查询并明确选择个人镜像")
	}
	return found[0], nil
}
