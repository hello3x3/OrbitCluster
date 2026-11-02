package cli

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const prefix = "/api/terminal/v1"

type client struct {
	s    settings
	http *http.Client
}

func newClient(s settings) (*client, error) {
	if s.Server == "" {
		return nil, configError("请先配置服务地址：orbit config set server http://集群地址")
	}
	tr := http.DefaultTransport.(*http.Transport).Clone()
	u, _ := url.Parse(s.Server)
	ip := net.ParseIP(u.Hostname())
	if ip != nil && (ip.IsPrivate() || ip.IsLoopback()) {
		tr.Proxy = nil
	}
	if s.CA != "" && u.Scheme == "https" {
		b, e := os.ReadFile(s.CA)
		if e != nil {
			return nil, configError("无法读取 CA 公共证书")
		}
		pool, e := x509.SystemCertPool()
		if e != nil {
			pool = x509.NewCertPool()
		}
		if !pool.AppendCertsFromPEM(b) {
			return nil, configError("CA 公共证书格式无效")
		}
		tr.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	}
	return &client{s, &http.Client{Transport: tr, Timeout: time.Duration(s.Timeout * float64(time.Second)), CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (c *client) sessions() (map[string]any, error) {
	if st, e := os.Lstat(c.s.Root); e == nil {
		if st.Mode()&os.ModeSymlink != 0 {
			return nil, configError("凭证目录不能为符号链接")
		}
		if e = checkOwner(c.s.Root, st); e != nil {
			return nil, e
		}
	}
	d, e := readObject(filepath.Join(c.s.Root, "auth.json"), true)
	if e != nil {
		return nil, e
	}
	if d["sessions"] == nil {
		return map[string]any{}, nil
	}
	s, ok := d["sessions"].(map[string]any)
	if !ok {
		return nil, configError("凭证文件格式错误，请重新登录")
	}
	return s, nil
}
func (c *client) credential(v any, remove bool) error {
	d, e := c.sessions()
	if e != nil {
		return e
	}
	if remove {
		delete(d, c.s.Server)
	} else {
		d[c.s.Server] = v
	}
	return writeObject(c.s.Root, "auth.json", map[string]any{"sessions": d})
}
func (c *client) request(method, path string, form url.Values, body any, authenticated bool) (any, error) {
	if !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") {
		return nil, configError("接口路径无效")
	}
	var data io.Reader
	ct := ""
	if body != nil {
		b, e := json.Marshal(body)
		if e != nil {
			return nil, configError("请求参数无效")
		}
		data = bytes.NewReader(b)
		ct = "application/json"
	} else if form != nil {
		data = strings.NewReader(form.Encode())
		ct = "application/x-www-form-urlencoded"
	}
	req, e := http.NewRequest(method, c.s.Server+prefix+path, data)
	if e != nil {
		return nil, configError("服务地址或参数无效")
	}
	req.Header.Set("Accept", "application/json")
	if ct != "" {
		req.Header.Set("Content-Type", ct)
	}
	if authenticated {
		all, e := c.sessions()
		if e != nil {
			return nil, e
		}
		v, ok := all[c.s.Server].(map[string]any)
		if !ok {
			return nil, authError()
		}
		expires, _ := v["expires_at"].(float64)
		token, _ := v["token"].(string)
		if expires <= float64(time.Now().Unix()) || token == "" {
			return nil, authError()
		}
		req.Header.Set("Authorization", "Bearer "+token)
	}
	response, e := c.http.Do(req)
	if e != nil {
		message := "无法连接服务器，请检查服务地址、网络或 HTTPS 证书"
		if method != "GET" {
			message += "；操作结果尚不确定，请先查询状态再决定是否重试"
		}
		return nil, &failure{message, "network_error", 4}
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 && response.StatusCode < 400 {
		return nil, &failure{"服务地址发生重定向，请配置最终地址；未转发凭证", "unexpected_redirect", 1}
	}
	if response.StatusCode == 401 && authenticated {
		_ = c.credential(nil, true)
		return nil, authError()
	}
	var payload struct {
		OK    *bool  `json:"ok"`
		Data  any    `json:"data"`
		Error string `json:"error"`
		Code  string `json:"code"`
	}
	dec := json.NewDecoder(io.LimitReader(response.Body, 16<<20))
	if dec.Decode(&payload) != nil || payload.OK == nil {
		return nil, &failure{"服务器未返回终端 API 数据，请确认已部署新接口", "invalid_response", 1}
	}
	if !*payload.OK || response.StatusCode >= 400 {
		if payload.Error == "" {
			payload.Error = "服务器拒绝操作"
		}
		if payload.Code == "" {
			payload.Code = "operation_rejected"
		}
		return nil, &failure{payload.Error, payload.Code, 1}
	}
	return payload.Data, nil
}
func (c *client) login(user, password, device string) (any, error) {
	if device == "" {
		device, _ = os.Hostname()
	}
	data, e := c.request("POST", "/auth/login", nil, map[string]any{"username": user, "password": password, "device": device}, false)
	if e != nil {
		return nil, e
	}
	d, ok := data.(map[string]any)
	if !ok {
		return nil, &failure{"服务器返回的登录凭证无效", "invalid_response", 1}
	}
	token, _ := d["token"].(string)
	username, _ := d["username"].(string)
	expires, _ := d["expires_at"].(float64)
	issued, _ := d["issued_at"].(float64)
	if len(token) < 43 || username == "" || expires <= float64(time.Now().Unix()) || issued <= 0 || issued >= expires || strings.ContainsAny(token, " \r\n\t") {
		return nil, &failure{"服务器返回的登录凭证无效", "invalid_response", 1}
	}
	if e = c.credential(map[string]any{"token": token, "username": username, "issued_at": issued, "expires_at": expires}, false); e != nil {
		return nil, e
	}
	delete(d, "token")
	delete(d, "token_type")
	return d, nil
}
