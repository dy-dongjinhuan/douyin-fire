#!/usr/bin/env node
'use strict';
// Douyin Fire - npm 启动器 / launcher
// 自动创建 Python 虚拟环境、安装依赖、下载 Chromium，并启动本地面板。
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const PKG = path.resolve(__dirname, '..');
const VENV = path.join(PKG, '.venv');
const isWin = process.platform === 'win32';
const venvPy = isWin
  ? path.join(VENV, 'Scripts', 'python.exe')
  : path.join(VENV, 'bin', 'python');

function log(...a) { console.log('\x1b[36m[douyin-fire]\x1b[0m', ...a); }
function err(...a) { console.error('\x1b[31m[douyin-fire]\x1b[0m', ...a); }

function findPython() {
  for (const c of ['python3', 'python']) {
    try {
      const r = spawnSync(c, ['--version'], { stdio: 'ignore' });
      if (r.status === 0) return c;
    } catch (e) { /* ignore */ }
  }
  return null;
}

function openBrowser(url) {
  try {
    const cmd = isWin ? 'cmd' : (process.platform === 'darwin' ? 'open' : 'xdg-open');
    const args = isWin ? ['/c', 'start', '', url] : [url];
    spawn(cmd, args, { stdio: 'ignore', detached: true, shell: isWin });
  } catch (e) { /* ignore */ }
}

function parseArgs(argv) {
  const o = { cmd: 'start', port: '8765', host: '127.0.0.1' };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === 'start' || a === 'stop' || a === 'status') o.cmd = a;
    else if (a === '--port') o.port = argv[++i];
    else if (a === '--host') o.host = argv[++i];
    else if (a.startsWith('--port=')) o.port = a.split('=')[1];
    else if (a.startsWith('--host=')) o.host = a.split('=')[1];
  }
  return o;
}

function ensureConfig() {
  const cfg = path.join(PKG, 'config.json');
  const ex = path.join(PKG, 'config.example.json');
  if (!fs.existsSync(cfg) && fs.existsSync(ex)) {
    fs.copyFileSync(ex, cfg);
    log('已生成 config.json（可稍后在后台修改好友/消息）');
  }
}

function ensureVenv() {
  if (fs.existsSync(venvPy)) return;
  const py = findPython();
  if (!py) {
    err('未检测到 Python 3.11+。请先安装并勾选 “Add Python to PATH”：');
    err('  https://www.python.org/downloads/');
    process.exit(1);
  }
  log('首次运行：创建虚拟环境并安装依赖（可能需几分钟）...');
  let r = spawnSync(py, ['-m', 'venv', VENV], { stdio: 'inherit' });
  if (r.status !== 0) { err('创建虚拟环境失败'); process.exit(1); }
  spawnSync(venvPy, ['-m', 'pip', 'install', '--upgrade', 'pip', '-q'], { stdio: 'inherit' });
  r = spawnSync(venvPy, ['-m', 'pip', 'install', '-r', path.join(PKG, 'requirements.txt'), '-q'], { stdio: 'inherit' });
  if (r.status !== 0) { err('依赖安装失败，请检查网络后重试'); process.exit(1); }
  log('下载 Chromium 浏览器（约 150MB，请耐心等待）...');
  r = spawnSync(venvPy, ['-m', 'playwright', 'install', 'chromium'], { stdio: 'inherit' });
  if (r.status !== 0) { err('Chromium 安装失败'); process.exit(1); }
}

let opt = parseArgs(process.argv);

function start() {
  ensureConfig();
  ensureVenv();
  const env = Object.assign({}, process.env, {
    DEPLOY_MODE: 'local',
    GUI_HOST: opt.host,
    GUI_PORT: String(opt.port),
    ADMIN_USER: process.env.ADMIN_USER || 'admin',
    ADMIN_PASSWORD: process.env.ADMIN_PASSWORD || 'admin',
  });
  log(`启动面板： http://${opt.host}:${opt.port}/  （local 本地模式：免登录，永久会员）`);
  const p = spawn(venvPy, ['gui.py'], { cwd: PKG, env, stdio: 'inherit' });
  p.on('error', (e) => { err('启动失败：', e.message); process.exit(1); });
  p.on('exit', (code) => { if (code !== 0 && code !== null) err('进程退出，代码', code); });
  setTimeout(() => openBrowser(`http://${opt.host}:${opt.port}/`), 4000);
  const kill = () => { try { p.kill(); } catch (e) {} process.exit(0); };
  process.on('SIGINT', kill);
  process.on('SIGTERM', kill);
}

function stop() {
  try {
    if (isWin) {
      spawnSync('powershell', ['-NoProfile', '-Command',
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -match 'gui\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        { stdio: 'ignore' });
    } else {
      spawnSync('pkill', ['-f', 'gui.py'], { stdio: 'ignore' });
    }
    log('已尝试停止 douyin-fire 服务');
  } catch (e) { err(e.message); }
}

if (opt.cmd === 'stop') stop();
else start();
