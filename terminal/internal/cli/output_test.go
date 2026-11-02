package cli

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

// Exercise Run, including real HTTP decoding and output selection. Dropping a
// status/error/identifier or accidentally changing the JSON contract must fail.
func outputCommand(t *testing.T, data any, args ...string) (string, string, int) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "data": data})
	}))
	t.Cleanup(srv.Close)
	a := testArgs(t, srv.URL)
	s, e := loadSettings(a)
	if e != nil {
		t.Fatal(e)
	}
	c, e := newClient(s)
	if e != nil {
		t.Fatal(e)
	}
	if e = c.credential(map[string]any{"token": strings.Repeat("T", 43), "expires_at": time.Now().Unix() + 604800}, false); e != nil {
		t.Fatal(e)
	}
	var out, err bytes.Buffer
	code := Run(args, strings.NewReader(""), &out, &err)
	return out.String(), err.String(), code
}

func instanceOutputFixture() map[string]any {
	return map[string]any{
		"id": 123, "job_id": 424242, "task_name": "训练实验", "res_name": "训练实验",
		"username": "alice", "plan_id": 1, "plan_name": "GPU实验套餐", "gpus": 1, "cpus": 8, "mem_gb": 32,
		"state": "RUNNING", "state_cn": "启动失败", "state_key": "failed", "slurm_state": "RUNNING",
		"node": "gpu01", "node_cn": "gpu01", "req_node": "", "ssh_ip": "192.0.2.11", "port": 23107,
		"account": "lab", "image": "/share/images/cuda.sqsh", "image_name": "cuda.sqsh",
		"walltime": "04:00:00", "hours": "04:00:00", "created_at": "2026-11-02T12:00:00",
		"created": "2026-11-02 12:00:00", "updated_at": "2026-11-02T12:01:00",
		"started_at": "2026-11-02T12:01:00", "started": "", "stopped_at": nil, "deadline_cn": "2026-11-02 16:01:00",
		"starting": false, "startup_error": "端口被占用，请换端口", "last_error": "端口被占用，请换端口",
		"saving": 0, "saving_now": false, "last_save": "保存失败：磁盘空间不足", "auto_saved_path": "", "auto_saved_at": nil,
		"can_stop": true, "can_save": true, "is_running": true,
	}
}

func TestDefaultResourceOutputKeepsContextWithoutDuplicateFields(t *testing.T) {
	data := []any{instanceOutputFixture()}
	out, err, code := outputCommand(t, data, "instances", "list")
	if code != 0 {
		t.Fatal(code, err)
	}
	for _, want := range []string{"我的资源", "123", "424242", "训练实验", "启动失败", "RUNNING", "gpu01", "lab", "8", "32", "04:00:00", "端口被占用，请换端口", "保存失败：磁盘空间不足"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
	for _, redundant := range []string{"\"state_key\"", "\"is_running\"", "\"res_name\"", "\"saving_now\""} {
		if strings.Contains(out, redundant) {
			t.Errorf("raw JSON field %s leaked: %s", redundant, out)
		}
	}
	if strings.Count(out, "端口被占用，请换端口") != 1 {
		t.Fatal("same failure repeated", out)
	}
	if json.Valid([]byte(out)) {
		t.Fatal("default is still a JSON dump", out)
	}
}

func TestDetailsKeepsFullPathsAndUnknownServerFields(t *testing.T) {
	data := instanceOutputFixture()
	data["scheduler_note"] = "等待维护窗口结束"
	out, err, code := outputCommand(t, data, "instances", "show", "123", "--details")
	if code != 0 {
		t.Fatal(code, err)
	}
	for _, want := range []string{"/share/images/cuda.sqsh", "192.0.2.11", "23107", "GPU实验套餐", "2026-11-02", "scheduler_note", "等待维护窗口结束"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
	if strings.Count(out, "端口被占用，请换端口") != 1 {
		t.Fatal(out)
	}
	if strings.Count(out, "424242") != 1 {
		t.Fatal("Slurm ID repeated", out)
	}
	if strings.Contains(out, "ssh -p") {
		t.Fatal("must not suggest connecting to a failed startup", out)
	}
}

func TestJSONOutputRetainsCompleteExistingContract(t *testing.T) {
	data := instanceOutputFixture()
	data["future_field"] = map[string]any{"empty": "", "zero": 0, "false": false}
	out, err, code := outputCommand(t, data, "instances", "show", "123", "--json")
	if code != 0 || err != "" {
		t.Fatal(code, out, err)
	}
	var got any
	if e := json.Unmarshal([]byte(out), &got); e != nil {
		t.Fatal(e, out)
	}
	wantBytes, _ := json.Marshal(map[string]any{"ok": true, "data": data})
	var want any
	_ = json.Unmarshal(wantBytes, &want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("JSON changed:\n%s", out)
	}
	if strings.Count(out, "\n") != 1 {
		t.Fatal("JSON must be one machine-readable record", out)
	}
}

func TestLogsPrintRealLinesAndEventsWithoutJSONEscaping(t *testing.T) {
	data := map[string]any{"job_id": 424242, "text": "epoch 1: loss=0.25\nepoch 2: loss=0.12\n", "log_error": "日志已轮转",
		"events": []any{map[string]any{"ts": "2026-11-02 12:00:00", "kind": "submit", "label": "提交", "message": "任务已提交"}}}
	out, err, code := outputCommand(t, data, "instances", "logs", "123")
	if code != 0 {
		t.Fatal(code, err)
	}
	for _, want := range []string{"123", "424242", "epoch 1: loss=0.25\nepoch 2: loss=0.12\n", "日志已轮转", "2026-11-02 12:00:00", "任务已提交"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q: %s", want, out)
		}
	}
	if strings.Contains(out, `\nepoch`) {
		t.Fatal("log lines escaped", out)
	}
}

func TestReadableViewsForQueriesEmptyAndPartialResults(t *testing.T) {
	tests := []struct {
		name  string
		args  []string
		data  any
		wants []string
	}{
		{"identity", []string{"whoami"}, map[string]any{"username": "alice", "display_name": "林晨", "issued_at": 1793577600, "expires_at": 1794182400}, []string{"alice", "林晨", "2026-", "到期"}},
		{"plans", []string{"plans"}, []any{map[string]any{"id": 1, "name": "训练套餐", "gpus": 1, "gpu_model": "RTX 4090", "cpus": 8, "mem_gb": 32, "maxtime_h": 48, "description": "共享GPU"}}, []string{"训练套餐", "RTX 4090", "48", "共享GPU"}},
		{"images", []string{"images", "list"}, map[string]any{"public": []any{map[string]any{"name": "cuda.sqsh", "path": "/share/images/cuda.sqsh", "size": 1073741824, "note": "CUDA基础环境", "mtime_cn": "2026-11-02 12:34", "created_at": ""}}, "mine": []any{}}, []string{"公共镜像", "/share/images/cuda.sqsh", "CUDA基础环境", "2026-11-02 12:34", "个人镜像", "暂无"}},
		{"accounts", []string{"accounts"}, map[string]any{"default": "lab", "accounts": []any{map[string]any{"name": "lab", "qos": "acct-lab", "priority": 0, "per_user_gpu_cap": 0, "is_default": true}}}, []string{"lab", "acct-lab", "不限", "默认"}},
		{"nodes", []string{"nodes"}, map[string]any{"nodes": []any{map[string]any{"name": "gpu01", "state": "mix", "gres_model": "RTX 4090", "gres_total": 4, "gres_used": 2, "gres_free": 2, "cpus_total": 64, "cpus_alloc": 16}}}, []string{"gpu01", "RTX 4090", "mix", "2/4", "16/64"}},
		{"empty", []string{"instances", "list"}, []any{}, []string{"暂无", "orbit plans"}},
		{"profile", []string{"profile", "show"}, map[string]any{"username": "alice", "can_apply": false, "os_ok": true, "keys": []any{}}, []string{"alice", "不可申请", "SSH"}},
		{"partial quota", []string{"quota"}, map[string]any{"disk": nil, "slurm": nil, "disk_err": "磁盘查询失败", "slurm_err": "Slurm暂时不可用", "os_ok": true}, []string{"磁盘查询失败", "Slurm暂时不可用"}},
		{"not ready", []string{"instances", "connection", "123"}, map[string]any{"id": 123, "job_id": 424242, "ready": false, "state": "RUNNING", "state_cn": "启动中", "command": "", "argv": []any{}, "error": ""}, []string{"123", "424242", "尚未就绪", "启动中"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			out, err, code := outputCommand(t, tt.data, tt.args...)
			if code != 0 {
				t.Fatal(code, err)
			}
			for _, want := range tt.wants {
				if !strings.Contains(out, want) {
					t.Errorf("missing %q:\n%s", want, out)
				}
			}
			if strings.Contains(out, "<nil>") {
				t.Fatal("Go representation leaked", out)
			}
		})
	}
}

func TestNarrowOutputDoesNotLoseUnicodeNames(t *testing.T) {
	t.Setenv("COLUMNS", "60")
	name := strings.Repeat("中英混合Experiment", 8)
	out, err, code := outputCommand(t, []any{map[string]any{"id": 1, "name": name, "gpus": 1, "gpu_model": "RTX 4090", "cpus": 8, "mem_gb": 32, "maxtime_h": 48}}, "plans")
	if code != 0 || !utf8.ValidString(out) {
		t.Fatal(code, out, err)
	}
	// Layout may wrap, but must retain every character rather than ellipsize it.
	compact := strings.Join(strings.Fields(out), "")
	if !strings.Contains(compact, name) {
		t.Fatal("long name was truncated", out)
	}
}
