import { buildInstallCommand } from './guide-install.mjs?v=3';
const server = document.getElementById('install-server');
const ca = document.getElementById('install-ca');
const platform = document.getElementById('install-platform');
const httpNotice = document.getElementById('install-http-notice');
const command = document.getElementById('install-command');
const status = document.getElementById('install-status');
const copy = document.getElementById('install-copy');
function update() {
  const isHttp = /^http:\/\//i.test(server.value.trim());
  ca.disabled = isHttp; httpNotice.hidden = !isHttp;
  try {
    command.value = buildInstallCommand(server.value, isHttp ? '' : ca.value, platform.value);
    copy.disabled = false; status.classList.remove('error'); status.textContent = '点击复制，然后在本机终端执行；安装过程会显示进度';
  } catch (e) {
    command.value = ''; copy.disabled = true; status.textContent = e.message; status.classList.add('error');
  }
}
if (['http:', 'https:'].includes(location.protocol)) server.value = location.origin;
if (/Win/i.test(navigator.userAgentData?.platform || navigator.platform)) platform.value = 'windows';
for (const field of [server, ca, platform]) field.addEventListener('input', update);
function legacyCopy() {
  command.focus(); command.select(); command.setSelectionRange(0, command.value.length);
  try { return document.execCommand('copy'); } catch { return false; }
}
copy.addEventListener('click', async () => {
  let copied = false;
  // HTTP private-network sites lack navigator.clipboard; use the click gesture synchronously.
  if (!window.isSecureContext || !navigator.clipboard?.writeText) copied = legacyCopy();
  else {
    try { await navigator.clipboard.writeText(command.value); copied = true; } catch { copied = legacyCopy(); }
  }
  if (copied) status.textContent = '安装命令已复制到剪贴板';
  else { command.focus(); command.select(); status.textContent = '浏览器阻止自动复制，已选中命令，请按 Ctrl+C 或 ⌘C'; }
});
update();
