#!/usr/bin/env node
'use strict';

/**
 * 火花续连 · 本地版一键启动器
 *
 * 行为对标 `npx @deepseek-ai/dsh web`：
 *   - `npx douyin-fire`         → 首次自动解包本地版、建 Python 虚拟环境、装依赖、起服务、开浏览器
 *   - `npx douyin-fire web`     → 同上
 *   - `npx douyin-fire --help`  → 帮助
 *
 * 本地版特性：免登录、免后台、免续费页，默认永久会员，无任何会员提示。
 */

const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const BUNDLED_APP = path.join(__dirname, '..', 'lib', 'app');
const VERSION = require('../package.json').version;

// 运行态目录：与 npm 缓存隔离，升级后仍能保留用户数据与配置
const HOME = process.env.DOUYIN_FIRE_HOME || path.join(os.homedir(), '.douyin-fire');
const APP = path.join(HOME, 'app');
const VENV = path.join(HOME, 'venv');
const MARKER = path.join(HOME, '.deps-installed');
const VERSION_FILE = path.join(HOME, '.version');

const PORT = process.env.GUI_PORT || '8765';
const URL = 'http://localhost:' + PORT;

function log(msg) { console.log('\x1b[36m[douyin-fire]\x1b[0m ' + msg); }
function warn(msg) { console.warn('\x1b[33m[douyin-fire]\x1b[0m ' + msg); }
function fail(msg) {
  console.error('\x1b[31m[douyin-fire] 错误：\x1b[0m' + msg);
  process.exit(1);
}

const IS_WIN = process.platform === 'win32';

function venvExe(name) {
  return IS_WIN ? path.join(VENV, 'Scripts', name + '.exe') : path.join(VENV, 'bin', name);
}
function venvPython() { return venvExe('python'); }
function venvPip() { return venvExe('pip'); }

// ---- Python 检测 ----
function findPython() {
  const candidates = ['python3', 'python', 'py'];
  for (const c of candidates) {
    try {
      const out = execFileSync(c, ['--version'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
      const m = out.match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/);
      if (m) {
        const major = +m[1], minor = +m[2];
        if (major === 3 && minor >= 10) return c;
      }
    } catch (e) { /* try next */ }
  }
  return null;
}

// ---- 文件拷贝 ----
function copyTree(src, dest, skip) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (skip && skip(entry.name)) continue;
    if (entry.isDirectory()) copyTree(s, d, skip);
    else fs.copyFileSync(s, d);
  }
}

function ensureApp(force) {
  const existing = fs.existsSync(APP);
  const installedVer = fs.existsSync(VERSION_FILE) ? fs.readFileSync(VERSION_FILE, 'utf8').trim() : '';

  if (!existing || force) {
    if (existing && force) {
      log('重置运行目录：' + APP);
      fs.rmSync(APP, { recursive: true, force: true });
    }
    log('首次解包本地版到 ' + APP);
    copyTree(BUNDLED_APP, APP, null);
  } else if (installedVer !== VERSION) {
    // 升级：覆盖代码但保留用户配置与数据
    log('检测到新版本（' + installedVer + ' → ' + VERSION + '），增量更新代码（保留 config.json 与 data/）');
    copyTree(BUNDLED_APP, APP, (n) => n === 'config.json' || n === 'data');
  }
  fs.writeFileSync(VERSION_FILE, VERSION);
}

// ---- 虚拟环境 ----
function ensureVenv(py) {
  if (fs.existsSync(venvPython())) return;
  log('创建 Python 虚拟环境...');
  try {
    execFileSync(py, ['-m', 'venv', VENV], { stdio: 'inherit' });
  } catch (e) {
    fail('创建虚拟环境失败。请确认已安装 Python 3.10+ 且 `python -m venv` 可用（Windows 需勾选“pip”与“venv”组件）。');
  }
}

// ---- 依赖安装 ----
function ensureDeps() {
  if (fs.existsSync(MARKER)) {
    log('依赖已就绪，跳过安装。');
    return;
  }
  const req = path.join(APP, 'requirements.txt');
  log('安装 Python 依赖（首次较慢，请耐心等待）...');
  try {
    execFileSync(venvPip(), ['install', '-r', req], { stdio: 'inherit' });
  } catch (e) {
    fail('依赖安装失败，请检查网络后重试。');
  }

  // Playwright Chromium 二进制（失败仅告警，不阻断 UI 启动；首次执行任务时才需要）
  log('下载 Playwright Chromium 浏览器（约 150MB，可稍后按需）...');
  try {
    execFileSync(venvPython(), ['-m', 'playwright', 'install', 'chromium'], { stdio: 'inherit' });
  } catch (e) {
    warn('Chromium 下载未成功（可能网络受限）。Web 控制台可正常打开，但执行续火花任务前需手动运行：');
    warn('  ' + venvPython() + ' -m playwright install chromium');
  }

  fs.writeFileSync(MARKER, new Date().toISOString());
}

// ---- 启动 ----
let child = null;
function launch(open) {
  log('正在启动本地服务（DEPLOY_MODE=local）...');
  const env = Object.assign({}, process.env, { DEPLOY_MODE: 'local', GUI_PORT: PORT });
  child = spawn(venvPython(), ['gui.py'], { cwd: APP, env, stdio: 'inherit' });
  child.on('exit', (code) => process.exit(code === null ? 0 : code));
  child.on('error', (err) => fail('启动失败：' + err.message));

  const shutdown = (sig) => { if (child) child.kill(sig); };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  if (open) {
    setTimeout(() => openBrowser(), 2500);
  }
}

function openBrowser() {
  const op = IS_WIN ? 'start' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  try {
    const p = spawn(op, [URL], { stdio: 'ignore', detached: true, shell: IS_WIN });
    p.on('error', () => {});
  } catch (e) { /* 忽略：用户可手动打开 */ }
}

function printHelp() {
  console.log(`\n火花续连 · 本地版 (douyin-fire v${VERSION})

用法:
  npx douyin-fire [命令] [选项]

命令:
  web            启动本地 Web 控制台（默认）
  (无命令)       等同于 web

选项:
  --no-browser  启动后不自动打开浏览器
  --port <n>    指定端口（默认 8765）
  --reset       清空运行目录并重新解包
  --help, -h    显示本帮助
  --version, -v 显示版本号

说明:
  - 本地版免登录、免后台、免续费页，默认永久会员，无任何会员提示。
  - 首次运行会解包程序到 ${HOME}，并自动创建 Python 虚拟环境、安装依赖。
  - 运行数据（配置、账号）保存在 ${HOME}，升级不会丢失。
  - 可通过环境变量 DOUYIN_FIRE_HOME 自定义运行目录。

示例:
  npx douyin-fire
  npx douyin-fire web --port 9000 --no-browser
`);
}

function main() {
  const argv = process.argv.slice(2);
  const args = argv.filter((a) => !a.startsWith('--'));
  const flags = argv.filter((a) => a.startsWith('--'));

  const noBrowser = flags.includes('--no-browser');
  const reset = flags.includes('--reset');
  const help = flags.includes('--help') || flags.includes('-h');
  const version = flags.includes('--version') || flags.includes('-v');

  const portFlag = flags.find((f) => f.startsWith('--port'));
  if (portFlag) {
    const v = portFlag.split('=')[1] || argv[argv.indexOf(portFlag) + 1];
    if (v) process.env.GUI_PORT = v;
  }

  if (help) return printHelp();
  if (version) { console.log('douyin-fire v' + VERSION); return; }

  const cmd = args[0] || 'web';
  if (!['web', 'local'].includes(cmd)) {
    warn('未知命令 "' + cmd + '"，默认启动 web 控制台。');
  }

  console.log('\n\x1b[35m\x1b[1m火花续连 · 本地版\x1b[0m  (v' + VERSION + ')');
  log('运行目录: ' + HOME);

  const py = findPython();
  if (!py) {
    fail('未检测到 Python 3.10+。请先安装：https://www.python.org/downloads/ （Windows 安装时务必勾选 “Add python.exe to PATH”）。');
  }
  log('使用 Python: ' + py);

  ensureApp(reset);
  ensureVenv(py);
  ensureDeps();
  log('控制台地址: \x1b[4m' + URL + '\x1b[0m  （Ctrl+C 退出）');
  launch(!noBrowser);
}

main();
