import { buildInstallCommand } from './guide-install.mjs?v=1';

const installer = document.getElementById('terminal-installer');
const server = document.getElementById('install-server');
const ca = document.getElementById('install-ca');
const command = document.getElementById('install-command');
const status = document.getElementById('install-status');
const copy = document.getElementById('install-copy');
const ref = installer.dataset.releaseRef;

function update() {
  status.classList.remove('error');
  try {
    command.value = buildInstallCommand(server.value, ca.value, ref);
    copy.disabled = false;
    status.textContent = '复制后在本机终端执行';
  } catch (error) {
    command.value = '';
    copy.disabled = true;
    status.textContent = server.value ? error.message : '先向管理员获取 HTTPS 服务地址，再填写到上方';
    status.classList.toggle('error', Boolean(server.value));
  }
}
if (location.protocol === 'https:') server.value = location.origin;
server.addEventListener('input', update);
ca.addEventListener('input', update);
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
