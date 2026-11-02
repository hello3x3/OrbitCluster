import { buildInstallCommand } from './guide-install.mjs?v=2';

const installer = document.getElementById('terminal-installer');
const server = document.getElementById('install-server');
const ca = document.getElementById('install-ca');
const allowHttp = document.getElementById('install-allow-http');
const httpNotice = document.getElementById('install-http-notice');
const command = document.getElementById('install-command');
const status = document.getElementById('install-status');
const copy = document.getElementById('install-copy');
const ref = installer.dataset.releaseRef;

function update() {
  status.classList.remove('error');
  const isHttp = /^http:\/\//i.test(server.value.trim());
  ca.disabled = isHttp;
  httpNotice.hidden = !isHttp;
  try {
    command.value = buildInstallCommand(server.value, isHttp ? '' : ca.value, ref, allowHttp.checked);
    copy.disabled = false;
    status.textContent = '复制后在本机终端执行';
  } catch (error) {
    command.value = '';
    copy.disabled = true;
    status.textContent = server.value ? error.message : '先向管理员获取服务地址，再填写到上方';
    status.classList.toggle('error', Boolean(server.value));
  }
}
if (['http:', 'https:'].includes(location.protocol)) server.value = location.origin;
server.addEventListener('input', update);
ca.addEventListener('input', update);
allowHttp.addEventListener('change', update);
copy.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(command.value);
    status.textContent = '安装命令已复制';
  } catch {
    command.focus();
    command.select();
    status.textContent = '已选中命令，请按 Ctrl+C 或 ⌘C 复制';
  }
});
update();
