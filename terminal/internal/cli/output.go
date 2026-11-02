package cli

// Text is a presentation of the existing API, never an input to business logic.
// --json bypasses this file and retains every original field and type.
import (
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
)

type textView struct {
	out     io.Writer
	width   int
	details bool
}

func writeText(out io.Writer, a arguments, data any) {
	width, _ := strconv.Atoi(os.Getenv("COLUMNS"))
	if width < 40 {
		width = 100
	}
	if width > 200 {
		width = 200
	}
	v := textView{out, width, a.Bool["details"]}
	command := strings.Join(a.Pos, " ")
	m := object(data)
	switch {
	case command == "login" || command == "whoami":
		if command == "login" {
			v.title("登录成功")
		} else {
			v.title("当前登录")
		}
		v.field("用户", identity(m))
		v.field("认证时间", timestamp(m["issued_at"]))
		v.field("到期时间", timestamp(m["expires_at"]))
		v.line("到期后运行 orbit login --username 用户名 重新登录；不会自动续期。")
	case command == "logout":
		if m["local_only"] == true {
			v.line("已清除本机登录。服务器凭证仍会按原期限到期。")
		} else {
			v.line("已退出登录，当前凭证已撤销。")
		}
	case command == "plans":
		v.plans(list(data))
	case command == "nodes":
		v.nodes(m)
	case command == "accounts":
		v.accounts(m)
	case command == "quota":
		v.quota(m)
	case command == "images list":
		v.images(m)
	case command == "instances list":
		v.instances(list(data))
	case strings.HasPrefix(command, "instances logs "):
		v.logs(a, m)
	case strings.HasPrefix(command, "instances connection ") || strings.HasPrefix(command, "instances wait ") && m["ready"] != nil:
		v.connection(m)
	case strings.HasPrefix(command, "instances show ") || strings.HasPrefix(command, "instances wait "):
		v.instance(m)
	case command == "profile show":
		v.title("个人资料")
		v.field("用户", identity(m))
		v.field("集群账号", yesNo(m["os_ok"], "已关联", "未关联"))
		v.field("资源申请", yesNo(m["can_apply"], "可申请", "不可申请；请在网页检查账号和 SSH 公钥设置"))
		v.keys(list(m["keys"]))
	case command == "keys list":
		v.keys(list(data))
	case command == "update":
		report, _ := data.(updateReport)
		v.title("CLI 更新")
		v.field("本机版本", report.Current)
		if report.Latest != "" {
			v.field("服务器版本", report.Latest)
		}
		v.line(report.Message)
		if report.LogFile != "" {
			v.field("更新失败时查看", report.LogFile)
		}
		if report.Command != "" && report.Status == "available" {
			v.line("执行：" + report.Command)
		}
	case strings.HasPrefix(command, "config "):
		v.title("客户端配置")
		v.field("服务器", scalar(m["server"]))
		v.field("允许 HTTP", scalar(m["allow_http"]))
		v.field("CA 证书", first(m, "ca_bundle"))
		v.field("请求超时", scalar(m["timeout"])+" 秒")
		v.field("轮询间隔", scalar(m["poll_seconds"])+" 秒")
		v.field("配置目录", scalar(m["config_dir"]))
	case command == "setup":
		v.title("客户端已安装 · " + first(m, "version"))
		v.field("命令路径", first(m, "launcher"))
		v.field("服务器", first(m, "server"))
		if m["legacy_python_removed"] == true {
			v.line("已移除旧客户端的专用 Python 环境。")
		}
		v.line("下一步：" + first(m, "next"))
	case command == "uninstall":
		if m["pending_process_exit"] == true {
			v.line("卸载已安排，将在当前进程退出后清理程序。")
		} else {
			v.line("客户端已卸载。")
		}
		if m["config_preserved"] == true {
			v.line("已保留本机配置和登录信息。")
		}
		v.line("服务器上的作业和数据保持原状。")
	default:
		v.operation(a, data)
	}
}

func object(data any) map[string]any { m, _ := data.(map[string]any); return m }
func list(data any) []any            { rows, _ := data.([]any); return rows }
func present(x any) bool             { return x != nil && x != "" }
func scalar(x any) string {
	if x == nil {
		return "—"
	}
	switch value := x.(type) {
	case string:
		if value == "" {
			return "—"
		}
		return cleanText(value)
	case bool:
		if value {
			return "是"
		}
		return "否"
	case float64:
		return strconv.FormatFloat(value, 'f', -1, 64)
	default:
		return fmt.Sprint(value)
	}
}
func first(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if present(m[k]) {
			return scalar(m[k])
		}
	}
	return "—"
}
func number(x any) (float64, bool) {
	if !present(x) {
		return 0, false
	}
	n, e := strconv.ParseFloat(fmt.Sprint(x), 64)
	return n, e == nil
}
func yesNo(x any, yes, no string) string {
	if x == true {
		return yes
	}
	if x == false {
		return no
	}
	return "未知"
}
func timestamp(x any) string {
	if n, ok := number(x); ok && n > 0 {
		return time.Unix(int64(n), 0).Local().Format("2006-01-02 15:04:05 -07:00")
	}
	return strings.ReplaceAll(scalar(x), "T", " ")
}
func identity(m map[string]any) string {
	name := first(m, "username")
	if present(m["display_name"]) && m["display_name"] != m["username"] {
		name += "（" + scalar(m["display_name"]) + "）"
	}
	return name
}
func status(m map[string]any) string {
	s, cn := first(m, "state"), first(m, "state_cn")
	if cn != "—" && s != "—" && cn != s {
		return cn + " (" + s + ")"
	}
	if cn != "—" {
		return cn
	}
	return s
}
func resources(m map[string]any) string {
	return scalar(m["gpus"]) + " GPU / " + scalar(m["cpus"]) + " CPU / " + scalar(m["mem_gb"]) + " GiB"
}
func size(x any, scale float64) string {
	n, ok := number(x)
	if !ok {
		return "—"
	}
	n *= scale
	units := []string{"B", "KiB", "MiB", "GiB", "TiB", "PiB"}
	i := 0
	for n >= 1024 && i < len(units)-1 {
		n /= 1024
		i++
	}
	digits := 1
	if n == float64(int64(n)) {
		digits = 0
	}
	return strconv.FormatFloat(n, 'f', digits, 64) + " " + units[i]
}
func capText(x any) string {
	if n, ok := number(x); ok && n == 0 {
		return "不限"
	}
	return scalar(x)
}
func (v textView) line(s string)  { fmt.Fprintln(v.out, cleanText(s)) }
func (v textView) title(s string) { v.line(s) }
func (v textView) field(label, value string) {
	// Keep copyable paths intact; terminal soft wrapping must not insert bytes
	// into a path that a user or agent may pass back to another command.
	if strings.HasPrefix(value, "/") || strings.HasPrefix(value, "http://") || strings.HasPrefix(value, "https://") || len(value) > 2 && value[1] == ':' && (value[2] == '\\' || value[2] == '/') {
		v.line("  " + label + "：" + value)
		return
	}
	for i, line := range strings.Split(cleanText(value), "\n") {
		prefix := "  " + label + "："
		if i > 0 {
			prefix = "    "
		}
		for j, part := range wrapText(line, v.width-displayWidth(prefix)) {
			if j > 0 {
				prefix = "    "
			}
			v.line(prefix + part)
		}
	}
}
func (v textView) optional(label string, value any) {
	if present(value) {
		v.field(label, scalar(value))
	}
}

func (v textView) instances(rows []any) {
	v.title(fmt.Sprintf("我的资源 · %d 项", len(rows)))
	if len(rows) == 0 {
		v.line("暂无资源。先用 orbit plans 和 orbit images list 选择套餐与镜像。")
		return
	}
	if v.details {
		for i, row := range rows {
			if i > 0 {
				v.line("")
			}
			v.instance(object(row))
		}
		return
	}
	for i, row := range rows {
		m := object(row)
		if i > 0 {
			v.line("")
		}
		v.line("#" + first(m, "id") + " · " + first(m, "task_name", "res_name", "plan_name") + " · " + status(m))
		v.field("作业 / 节点 / 账户", first(m, "job_id")+" / "+first(m, "node", "node_cn")+" / "+first(m, "account"))
		v.field("资源 / 时长", resources(m)+" / "+first(m, "walltime", "hours"))
		v.field("申请 / 到期", timestamp(m["created_at"])+" / "+first(m, "deadline_cn"))
		v.instanceNotes(m)
	}
	v.line("\n详情：orbit instances show ID；连接：orbit instances connection ID。ID 是门户实例编号。")
}
func (v textView) instanceNotes(m map[string]any) {
	seen := map[string]bool{}
	for _, f := range []struct{ key, label string }{{"startup_error", "启动异常"}, {"last_error", "最近异常"}, {"last_save", "最近保存"}} {
		if present(m[f.key]) {
			s := scalar(m[f.key])
			if !seen[s] {
				v.field(f.label, s)
				seen[s] = true
			}
		}
	}
	if m["saving_now"] == true {
		v.line("  镜像保存：进行中")
	}
}
func (v textView) instance(m map[string]any) {
	v.title("资源 #" + first(m, "id") + " · " + first(m, "task_name", "res_name", "plan_name"))
	v.field("状态", status(m))
	v.field("Slurm 作业", first(m, "job_id"))
	v.field("用户 / 账户", first(m, "username")+" / "+first(m, "account"))
	v.field("节点", first(m, "node", "node_cn"))
	v.optional("指定节点", m["req_node"])
	v.field("套餐", first(m, "plan_name", "plan_id"))
	v.field("配置", resources(m))
	v.field("镜像", first(m, "image", "image_name"))
	v.field("申请时长", first(m, "walltime", "hours"))
	v.field("SSH 地址 / 端口", first(m, "ssh_ip")+" / "+first(m, "port"))
	v.field("申请时间", timestamp(m["created_at"]))
	v.optional("开始时间", m["started_at"])
	v.optional("到期时间", m["deadline_cn"])
	v.optional("停止时间", m["stopped_at"])
	v.instanceNotes(m)
	v.optional("已保存镜像", m["auto_saved_path"])
	v.optional("自动保存时间", m["auto_saved_at"])
	if v.details {
		v.extras(m, "id job_id task_name res_name plan_id plan_name state state_cn state_key slurm_state username account node node_cn req_node gpus cpus mem_gb image image_name walltime hours ssh_ip port created created_at started started_at stopped_at startup_error last_error saving saving_now last_save deadline_cn auto_saved_path auto_saved_at is_running", 0)
		if present(m["slurm_state"]) && m["slurm_state"] != m["state"] {
			v.field("Slurm 原始状态", scalar(m["slurm_state"]))
		}
	}
	v.line("连接信息：orbit instances connection " + first(m, "id"))
}
func (v textView) connection(m map[string]any) {
	v.title("资源 #" + first(m, "id") + " · SSH 连接")
	v.field("作业 / 状态", first(m, "job_id")+" / "+status(m))
	if m["ready"] == true && present(m["command"]) {
		v.line("连接已就绪：")
		v.line("  " + scalar(m["command"]))
	} else {
		v.line("SSH 尚未就绪。")
		v.optional("原因", m["error"])
		v.line("等待：orbit instances wait " + first(m, "id") + " --until ssh-ready")
	}
	if v.details {
		v.extras(m, "id job_id state state_cn ready command error", 0)
	}
}
func (v textView) logs(a arguments, m map[string]any) {
	v.title("资源 #" + a.Pos[2] + " · 作业 " + first(m, "job_id") + " · 日志")
	v.optional("日志提示", m["log_error"])
	if present(m["text"]) {
		v.line("\n容器日志")
		text := cleanText(scalar(m["text"]))
		fmt.Fprint(v.out, text)
		if !strings.HasSuffix(text, "\n") {
			v.line("")
		}
	} else {
		v.line("暂无容器日志。")
	}
	if events := list(m["events"]); len(events) > 0 {
		v.line("\n门户事件")
		for _, event := range events {
			e := object(event)
			v.line("  " + first(e, "ts") + " [" + first(e, "label", "kind") + "] " + first(e, "message"))
		}
	}
}
func (v textView) plans(rows []any) {
	v.title(fmt.Sprintf("可用套餐 · %d 项", len(rows)))
	if len(rows) == 0 {
		v.line("暂无可用套餐。")
		return
	}
	table := [][]string{}
	for _, row := range rows {
		m := object(row)
		if v.details {
			v.generic(m, 0)
			v.line("")
			continue
		}
		table = append(table, []string{first(m, "id"), first(m, "name"), first(m, "gpu_model"), resources(m), scalar(m["maxtime_h"]) + " 小时", first(m, "description")})
	}
	if !v.details {
		v.table([]string{"ID", "套餐", "GPU 型号", "资源配置", "最长时长", "说明"}, table)
	}
}
func (v textView) nodes(m map[string]any) {
	rows := list(m["nodes"])
	v.title(fmt.Sprintf("集群节点 · %d 项", len(rows)))
	v.optional("分区", m["partition"])
	if len(rows) == 0 {
		v.line("暂无节点数据。")
		v.extras(m, "nodes partition", 0)
		return
	}
	table := [][]string{}
	for _, row := range rows {
		n := object(row)
		if v.details {
			v.generic(n, 0)
			v.line("")
			continue
		}
		table = append(table, []string{first(n, "name"), first(n, "state"), first(n, "gres_model"), scalar(n["gres_used"]) + "/" + scalar(n["gres_total"]), scalar(n["gres_free"]), scalar(n["cpus_alloc"]) + "/" + scalar(n["cpus_total"])})
	}
	if !v.details {
		v.table([]string{"节点", "状态", "GPU 型号", "GPU 已用/总量", "GPU 空闲", "CPU 已用/总量"}, table)
		v.line("完整节点信息：orbit nodes --details")
	}
}
func (v textView) accounts(m map[string]any) {
	rows := list(m["accounts"])
	v.title(fmt.Sprintf("可用账户 · %d 项", len(rows)))
	v.optional("默认账户", m["default"])
	if len(rows) == 0 {
		v.line("暂无账户数据。")
		return
	}
	table := [][]string{}
	for _, row := range rows {
		a := object(row)
		marker := "—"
		if a["is_default"] == true {
			marker = "默认"
		}
		table = append(table, []string{first(a, "name"), first(a, "qos"), scalar(a["priority"]), capText(a["per_user_gpu_cap"]), marker})
		if v.details {
			v.generic(a, 0)
		}
	}
	if !v.details {
		v.table([]string{"账户", "QoS", "优先级", "单用户 GPU 上限", "备注"}, table)
	}
}
func (v textView) quota(m map[string]any) {
	v.title("我的配额与额度")
	v.optional("提示", m["note"])
	if d := object(m["disk"]); d != nil {
		used := first(d, "used_h")
		if used == "—" {
			used = size(d["used_kb"], 1024)
		}
		hard := first(d, "hard_h")
		if hard == "—" {
			if n, ok := number(d["hard_kb"]); ok && n == 0 {
				hard = "不限"
			} else {
				hard = size(d["hard_kb"], 1024)
			}
		}
		v.field("磁盘已用 / 硬上限", used+" / "+hard)
		v.optional("磁盘软上限", d["soft_h"])
		v.optional("文件数量", d["files_used"])
		v.optional("磁盘说明", d["note"])
		if v.details {
			v.extras(d, "used_h hard_h soft_h note files_used", 0)
		}
	} else {
		v.line("磁盘配额：暂无数据。")
	}
	v.optional("磁盘异常", m["disk_err"])
	if s := object(m["slurm"]); s != nil {
		v.optional("调度总限额", s["total"])
		if len(list(m["accounts"])) == 0 {
			v.optional("Slurm 账户", s["accounts"])
			v.optional("QoS", s["qos"])
			v.optional("关联优先级", s["assoc_priority"])
		}
		if v.details {
			v.extras(s, "total accounts qos assoc_priority", 0)
		}
	}
	v.optional("调度异常", m["slurm_err"])
	if len(list(m["accounts"])) > 0 {
		v.line("")
		v.accounts(map[string]any{"accounts": m["accounts"], "default": m["default_account"]})
		v.line("多账户额度可叠加，仍受集群物理 GPU 数量限制。")
	}
}
func (v textView) images(m map[string]any) {
	for i, group := range []struct{ key, title string }{{"public", "公共镜像"}, {"mine", "个人镜像"}} {
		if i > 0 {
			v.line("")
		}
		rows := list(m[group.key])
		v.title(fmt.Sprintf("%s · %d 项", group.title, len(rows)))
		if len(rows) == 0 {
			v.line("暂无镜像。")
			continue
		}
		table := [][]string{}
		for _, row := range rows {
			image := object(row)
			if v.details {
				v.generic(image, 0)
				v.line("")
				continue
			}
			table = append(table, []string{first(image, "path", "name"), size(image["size"], 1), first(image, "created_at", "mtime_cn"), first(image, "note")})
		}
		if !v.details {
			v.table([]string{"镜像路径", "大小", "时间", "说明"}, table)
		}
	}
	v.line("\n申请时可用完整镜像路径作为 --image，避免同名混淆。")
}
func (v textView) keys(rows []any) {
	v.title(fmt.Sprintf("SSH 公钥 · %d 把", len(rows)))
	if len(rows) == 0 {
		v.line("暂无 SSH 公钥。添加：orbit keys add --file 本机公钥.pub")
		return
	}
	table := [][]string{}
	for _, row := range rows {
		k := object(row)
		if v.details {
			v.generic(k, 0)
			v.line("")
			continue
		}
		table = append(table, []string{first(k, "id"), first(k, "label"), keyFingerprint(first(k, "pubkey")), first(k, "added_at")})
	}
	if !v.details {
		v.table([]string{"ID", "名称", "公钥指纹", "添加时间"}, table)
	}
}
func keyFingerprint(key string) string {
	parts := strings.Fields(key)
	if len(parts) < 2 {
		return "—"
	}
	b, e := base64.StdEncoding.DecodeString(parts[1])
	if e != nil {
		return parts[0]
	}
	hash := sha256.Sum256(b)
	return parts[0] + " SHA256:" + base64.RawStdEncoding.EncodeToString(hash[:])
}
func (v textView) operation(a arguments, data any) {
	command := strings.Join(a.Pos, " ")
	m := object(data)
	message := "操作已提交。"
	switch {
	case command == "instances create":
		message = "资源申请已提交，等待服务器调度。"
	case len(a.Pos) > 1 && a.Pos[0] == "instances" && a.Pos[1] == "stop":
		message = "停机请求已提交。"
	case len(a.Pos) > 1 && a.Pos[0] == "instances" && a.Pos[1] == "restart":
		message = "重新启动请求已提交。"
	case len(a.Pos) > 1 && a.Pos[0] == "instances" && a.Pos[1] == "delete":
		message = "资源删除请求已完成。"
	case len(a.Pos) > 1 && (a.Pos[0] == "instances" || a.Pos[0] == "images") && a.Pos[1] == "save":
		message = "镜像保存请求已提交。"
	case len(a.Pos) > 1 && a.Pos[0] == "images":
		message = "镜像操作已完成。"
	case len(a.Pos) > 1 && a.Pos[0] == "keys":
		message = "SSH 公钥操作已完成。"
	case command == "profile password":
		message = "密码已修改，请重新登录。"
	case strings.HasPrefix(command, "profile name "):
		message = "显示名称已修改。"
	}
	v.line(message)
	if len(m) > 0 {
		v.generic(m, 0)
	}
	if command == "instances create" && present(m["instance_id"]) {
		v.line("查看：orbit instances show " + scalar(m["instance_id"]))
	}
}

// Generic nested details also keep future server fields visible, without JSON
// punctuation or the Go map[...] representation.
func (v textView) extras(m map[string]any, skip string, depth int) {
	ignored := map[string]bool{}
	for _, k := range strings.Fields(skip) {
		ignored[k] = true
	}
	keys := []string{}
	for k := range m {
		if !ignored[k] {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	for _, k := range keys {
		v.named(k, m[k], depth)
	}
}
func (v textView) generic(m map[string]any, depth int) { v.extras(m, "", depth) }

var fieldLabels = map[string]string{"id": "ID", "instance_id": "资源 ID", "job_id": "Slurm 作业", "name": "名称", "description": "说明", "gpus": "GPU 数量", "gpu_model": "GPU 型号", "cpus": "CPU 核数", "mem_gb": "内存 (GiB)", "maxtime_h": "最长时长 (小时)", "path": "路径", "size": "大小 (字节)", "note": "说明", "created_at": "创建时间", "updated_at": "更新时间", "added_at": "添加时间", "label": "名称", "pubkey": "公钥", "username": "用户名", "display_name": "显示名称", "priority": "优先级", "qos": "QoS", "per_user_gpu_cap": "单用户 GPU 上限", "is_default": "默认账户", "can_stop": "可停止", "can_save": "可保存", "enabled": "已启用"}

func (v textView) named(key string, value any, depth int) {
	label := fieldLabels[key]
	if label == "" {
		label = key
	}
	label = strings.Repeat("  ", depth) + label
	if m, ok := value.(map[string]any); ok {
		v.line("  " + label + "：")
		v.generic(m, depth+1)
		return
	}
	if rows, ok := value.([]any); ok {
		if len(rows) == 0 {
			v.field(label, "无")
			return
		}
		v.line("  " + label + "：")
		for i, row := range rows {
			if m, ok := row.(map[string]any); ok {
				v.line(fmt.Sprintf("    %d.", i+1))
				v.generic(m, depth+1)
			} else {
				v.field(strings.Repeat("  ", depth+1)+"-", scalar(row))
			}
		}
		return
	}
	v.field(label, scalar(value))
}

func (v textView) table(headers []string, rows [][]string) {
	widths := make([]int, len(headers))
	vertical := false
	for i, h := range headers {
		widths[i] = displayWidth(h)
	}
	for _, row := range rows {
		for i, cell := range row {
			w := displayWidth(cell)
			if w > v.width/2 {
				vertical = true
			}
			if w > widths[i] {
				widths[i] = w
			}
		}
	}
	total := func() int {
		n := 2 * (len(widths) - 1)
		for _, w := range widths {
			n += w
		}
		return n
	}
	for !vertical && total() > v.width {
		largest := -1
		for i, w := range widths {
			min := displayWidth(headers[i])
			if min < 6 {
				min = 6
			}
			if w > min && (largest < 0 || w > widths[largest]) {
				largest = i
			}
		}
		if largest < 0 {
			vertical = true
			break
		}
		widths[largest]--
	}
	if vertical {
		for i, row := range rows {
			if i > 0 {
				v.line("")
			}
			for j, cell := range row {
				v.field(headers[j], cell)
			}
		}
		return
	}
	writeRow := func(row []string) {
		wrapped := make([][]string, len(row))
		height := 1
		for i, cell := range row {
			wrapped[i] = wrapText(cell, widths[i])
			if len(wrapped[i]) > height {
				height = len(wrapped[i])
			}
		}
		for line := 0; line < height; line++ {
			cells := []string{}
			for i, lines := range wrapped {
				cell := ""
				if line < len(lines) {
					cell = lines[line]
				}
				if i < len(row)-1 {
					cell += strings.Repeat(" ", widths[i]-displayWidth(cell))
				}
				cells = append(cells, cell)
			}
			v.line(strings.TrimRight(strings.Join(cells, "  "), " "))
		}
	}
	writeRow(headers)
	sep := make([]string, len(headers))
	for i, w := range widths {
		sep[i] = strings.Repeat("─", w)
	}
	v.line(strings.Join(sep, "  "))
	for _, row := range rows {
		writeRow(row)
	}
}
func cleanText(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r == '\n' {
			b.WriteRune(r)
		} else if r == '\t' {
			b.WriteString("    ")
		} else if unicode.IsControl(r) || r == 0x7f {
			fmt.Fprintf(&b, "\\u%04x", r)
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}
func runeWidth(r rune) int {
	if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r) || r == 0x200d {
		return 0
	}
	if unicode.Is(unicode.Han, r) || unicode.Is(unicode.Hangul, r) || unicode.Is(unicode.Hiragana, r) || unicode.Is(unicode.Katakana, r) || r >= 0xff01 && r <= 0xff60 || r >= 0x1f300 && r <= 0x1faff || r >= 0x3000 && r <= 0x303f {
		return 2
	}
	return 1
}
func displayWidth(s string) int {
	width, max := 0, 0
	for _, r := range s {
		if r == '\n' {
			if width > max {
				max = width
			}
			width = 0
		} else {
			width += runeWidth(r)
		}
	}
	if width > max {
		max = width
	}
	return max
}
func wrapText(s string, width int) []string {
	if width < 4 {
		width = 4
	}
	lines := []string{}
	var b strings.Builder
	used := 0
	for _, r := range cleanText(s) {
		w := runeWidth(r)
		if r == '\n' {
			lines = append(lines, b.String())
			b.Reset()
			used = 0
			continue
		}
		if used+w > width {
			lines = append(lines, b.String())
			b.Reset()
			used = 0
		}
		b.WriteRune(r)
		used += w
	}
	return append(lines, b.String())
}
