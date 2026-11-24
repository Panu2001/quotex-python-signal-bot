import 'dotenv/config';
import express from 'express';
import { createServer as createViteServer } from 'vite';
import { spawn, type ChildProcess } from 'child_process';
import fs from 'fs';
import path from 'path';
import JSZip from 'jszip';

const PORT = Number(process.env.SIGNALDESK_PORT || 3000);
const PYTHON_COMMAND = process.env.SIGNALDESK_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const ROOT_DIR = process.cwd();
const PYTHON_DIR = path.join(ROOT_DIR, 'python_bot');
const WINDOWS_DIR = path.join(ROOT_DIR, 'windows_app');
const DIST_DIR = path.join(ROOT_DIR, 'dist');
const ENGINE_PATH = path.join(PYTHON_DIR, 'engine.py');
const DESKTOP_SERVER_PATH = path.join(PYTHON_DIR, 'desktop_app.py');
// ---------------------------------------------------------------------------
// Persistent Python Worker (eliminates ~500ms process spawn per request and
// preserves warm candle caches + live pyquotex WebSocket sessions in memory)
// ---------------------------------------------------------------------------
let pyWorkerProc: ChildProcess | null = null;
let pyWorkerPort: number | null = null;
let pyWorkerStarting: Promise<number> | null = null;

function stopPythonWorker(): void {
  if (pyWorkerProc && !pyWorkerProc.killed) {
    try {
      pyWorkerProc.kill('SIGTERM');
    } catch {
      // ignore cleanup errors
    }
  }
  pyWorkerProc = null;
  pyWorkerPort = null;
}

process.once('SIGINT', stopPythonWorker);
process.once('SIGTERM', stopPythonWorker);
process.once('exit', stopPythonWorker);

function ensurePythonWorker(): Promise<number> {
  if (pyWorkerPort !== null && pyWorkerProc && pyWorkerProc.exitCode === null && !pyWorkerProc.killed) {
    return Promise.resolve(pyWorkerPort);
  }
  if (pyWorkerStarting) {
    return pyWorkerStarting;
  }

  pyWorkerStarting = new Promise<number>((resolve, reject) => {
    const proc = spawn(PYTHON_COMMAND, [DESKTOP_SERVER_PATH, '--headless-server'], {
      cwd: PYTHON_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    pyWorkerProc = proc;

    let buf = '';
    let stderrBuf = '';
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        pyWorkerStarting = null;
        try {
          proc.kill('SIGKILL');
        } catch {
          // ignore
        }
        reject(new Error(`Persistent Python worker timed out starting: ${stderrBuf.slice(0, 200)}`));
      }
    }, 10000);

    proc.stdout?.on('data', (chunk: Buffer) => {
      buf += chunk.toString('utf8');
      const m = buf.match(/PORT=(\d+)/);
      if (m && !settled) {
        settled = true;
        clearTimeout(timer);
        pyWorkerPort = Number(m[1]);
        pyWorkerStarting = null;
        resolve(pyWorkerPort);
      }
    });

    proc.stderr?.on('data', (chunk: Buffer) => {
      stderrBuf += chunk.toString('utf8');
    });

    proc.on('error', (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        pyWorkerStarting = null;
        reject(err);
      }
    });

    proc.on('exit', () => {
      if (pyWorkerProc === proc) {
        pyWorkerProc = null;
        pyWorkerPort = null;
      }
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        pyWorkerStarting = null;
        reject(new Error(`Persistent Python worker exited early: ${stderrBuf.slice(0, 200)}`));
      }
    });
  });

  return pyWorkerStarting;
}

function runPythonEngineOneShot(payload: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON_COMMAND, [ENGINE_PATH], {
      cwd: PYTHON_DIR,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    const timer = setTimeout(() => {
      proc.kill('SIGKILL');
      reject(new Error('Python engine execution timed out'));
    }, 35000);

    proc.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString('utf8');
    });

    proc.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString('utf8');
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });

    proc.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(stderr || `Python process exited with code ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error(`Invalid JSON from Python engine: ${stdout.slice(0, 200)}`));
      }
    });

    proc.stdin.write(JSON.stringify(payload));
    proc.stdin.end();
  });
}

async function runPythonEngine(payload: Record<string, unknown>): Promise<unknown> {
  const loginAction = payload.action === 'quotex_test' || payload.action === 'quotex_verify_otp' || payload.action === 'quotex_cancel_otp';
  try {
    const port = await ensurePythonWorker();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), loginAction ? 85_000 : 32_000);
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/api/dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const json = await resp.json();
      if (!resp.ok && json && typeof json === 'object' && 'error' in json) {
        throw new Error(String((json as { error: unknown }).error));
      }
      return json;
    } finally {
      clearTimeout(timeoutId);
    }
  } catch (err) {
    // If persistent worker transiently restarted/disconnected, retry once via one-shot fallback
    if (pyWorkerProc && pyWorkerProc.exitCode !== null) {
      pyWorkerPort = null;
      pyWorkerProc = null;
    }
    const msg = err instanceof Error ? err.message : '';
    if (!loginAction && (msg.includes('fetch failed') || msg.includes('ECONNREFUSED') || msg.includes('worker'))) {
      return runPythonEngineOneShot(payload);
    }
    throw err;
  }
}

async function startServer() {
  const app = express();
  app.use(express.json({ limit: '10mb' }));

  // Start persistent Python worker immediately so caches prewarm before first browser request
  void ensurePythonWorker().catch(() => {
    // Will lazily retry on first API request
  });

  app.get('/api/catalog', async (_req, res) => {
    try {
      const data = await runPythonEngine({ action: 'catalog' });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Catalog error' });
    }
  });

  app.post('/api/assets/add', async (req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'add_asset',
        ...(req.body ?? {}),
      });
      res.json(data);
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : 'Add asset error' });
    }
  });

  app.post('/api/assets/remove', async (req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'remove_asset',
        symbol: req.body?.symbol ?? '',
      });
      res.json(data);
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : 'Remove asset error' });
    }
  });

  const analyzeCache = new Map<string, { ts: number; data: unknown }>();

  app.post('/api/analyze', async (req, res) => {
    try {
      const {
        symbol = 'EURUSD',
        timeframe = '1m',
        config = {},
        force_refresh = false,
      } = req.body ?? {};
      const cacheKey = `${symbol}|${timeframe}|${JSON.stringify({
        min_confidence: config?.min_confidence,
        rsi_oversold: config?.rsi_oversold,
        rsi_overbought: config?.rsi_overbought,
        bb_period: config?.bb_period,
        bb_std: config?.bb_std,
        ema_fast: config?.ema_fast,
        ema_slow: config?.ema_slow,
        adx_threshold: config?.adx_threshold,
        enabled_strategies: config?.enabled_strategies,
        strategy_weights: config?.strategy_weights,
        base_stake: config?.base_stake,
        risk_mode: config?.risk_mode,
      })}`;
      const ttlMs = timeframe === '5s' || timeframe === '10s' || timeframe === '15s' || timeframe === '30s' ? 1200 : 1800;
      const now = Date.now();
      const cached = analyzeCache.get(cacheKey);
      if (!force_refresh && cached && now - cached.ts < ttlMs) {
        res.json(cached.data);
        return;
      }

      const data = await runPythonEngine({
        action: 'analyze',
        symbol,
        timeframe,
        config,
        force_refresh: Boolean(force_refresh),
      });
      analyzeCache.delete(cacheKey);
      analyzeCache.set(cacheKey, { ts: Date.now(), data });
      if (analyzeCache.size > 60) {
        const oldest = analyzeCache.keys().next().value;
        if (oldest) analyzeCache.delete(oldest);
      }
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Analyze error' });
    }
  });

  app.post('/api/scan', async (req, res) => {
    try {
      const { timeframe = '1m', active_symbol = '', config = {} } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'scan_all',
        timeframe,
        active_symbol,
        config,
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Scan error' });
    }
  });

  app.post('/api/backtest', async (req, res) => {
    try {
      const payload = {
        action: 'backtest',
        ...(req.body ?? {}),
      };
      const data = await runPythonEngine(payload);
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Backtest error' });
    }
  });

  app.post('/api/telegram/detect-chat', async (req, res) => {
    try {
      const { bot_token = '' } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'telegram_detect_chat',
        bot_token,
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Telegram detect error' });
    }
  });

  app.post('/api/telegram/test', async (req, res) => {
    try {
      const { bot_token = '', chat_id = '', message = '' } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'telegram_test',
        bot_token,
        chat_id,
        message,
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Telegram error' });
    }
  });

  app.post('/api/telegram/signal', async (req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'telegram_signal',
        ...(req.body ?? {}),
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Telegram signal error' });
    }
  });

  app.post('/api/quotex/browser', (_req, res) => {
    res.status(501).json({ ok: false, error: 'Quotex Browser is available in the Windows app.' });
  });

  app.post('/api/quotex/test', async (req, res) => {
    try {
      const {
        auth_method = 'ssid',
        ssid = '',
        cookie = '',
        user_agent = '',
        email = '',
        password = '',
        pin = '',
        account_type = 'DEMO',
        force_fresh = false,
      } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'quotex_test',
        auth_method,
        ssid,
        cookie,
        user_agent,
        email,
        password,
        pin,
        account_type,
        force_fresh: Boolean(force_fresh),
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Quotex test error' });
    }
  });

  app.post('/api/quotex/verify-otp', async (req, res) => {
    try {
      const { login_id = '', otp = '' } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'quotex_verify_otp',
        login_id,
        otp,
      });
      res.json(data);
    } catch (err) {
      const timedOut = err instanceof Error && err.name === 'AbortError';
      res.status(timedOut ? 504 : 503).json({
        mode: 'OTP_UNAVAILABLE',
        message: timedOut
          ? 'The login server did not answer in time. Check the connection status before requesting another code.'
          : 'The login server could not confirm the code. Check the connection status before retrying.',
      });
    }
  });

  app.post('/api/quotex/cancel-otp', async (req, res) => {
    try {
      const { login_id = '' } = req.body ?? {};
      const data = await runPythonEngine({
        action: 'quotex_cancel_otp',
        login_id,
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'OTP cancel error' });
    }
  });

  app.get('/api/quotex/status', async (_req, res) => {
    try {
      const data = await runPythonEngine({ action: 'quotex_status' });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Quotex status error' });
    }
  });

  app.post('/api/quotex/disconnect', async (_req, res) => {
    try {
      const data = await runPythonEngine({ action: 'quotex_disconnect' });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Quotex disconnect error' });
    }
  });

  app.post('/api/quotex/sync-assets', async (_req, res) => {
    try {
      const data = await runPythonEngine({ action: 'quotex_sync_assets' });
      res.json(data);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Quotex sync error' });
    }
  });

  app.get('/api/bot-files', (_req, res) => {
    try {
      const entries = fs.readdirSync(PYTHON_DIR).sort();
      const files = entries
        .filter((name) => !name.startsWith('__') && !name.endsWith('.pyc'))
        .map((name) => {
          const full = path.join(PYTHON_DIR, name);
          const stat = fs.statSync(full);
          if (!stat.isFile()) return null;
          return {
            name,
            size: stat.size,
            content: fs.readFileSync(full, 'utf8'),
          };
        })
        .filter(Boolean);
      res.json({ files });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'File read error' });
    }
  });

  app.post('/api/bot-download', async (req, res) => {
    try {
      const customConfig = req.body?.config as Record<string, unknown> | undefined;
      const zip = new JSZip();
      const folder = zip.folder('quotex_signal_bot');
      if (!folder) {
        res.status(500).json({ error: 'Failed to create zip folder' });
        return;
      }

      const entries = fs.readdirSync(PYTHON_DIR).sort();
      for (const name of entries) {
        if (name.startsWith('__') || name.endsWith('.pyc')) continue;
        const full = path.join(PYTHON_DIR, name);
        const stat = fs.statSync(full);
        if (!stat.isFile()) continue;

        if (name === 'config.json' && customConfig) {
          folder.file('config.json', JSON.stringify(customConfig, null, 2));
        } else {
          folder.file(name, fs.readFileSync(full, 'utf8'));
        }
      }

      const buffer = await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
      res.setHeader('Content-Type', 'application/zip');
      res.setHeader('Content-Disposition', 'attachment; filename="quotex_python_signal_bot.zip"');
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Download error' });
    }
  });

  app.get('/api/windows-files', (_req, res) => {
    try {
      const textExts = new Set(['.bat', '.ps1', '.spec', '.iss', '.txt', '.py']);
      const files: Array<{ name: string; size: number; content: string }> = [];

      if (fs.existsSync(WINDOWS_DIR)) {
        for (const name of fs.readdirSync(WINDOWS_DIR).sort()) {
          const full = path.join(WINDOWS_DIR, name);
          const stat = fs.statSync(full);
          if (!stat.isFile() || !textExts.has(path.extname(name).toLowerCase())) continue;
          files.push({
            name,
            size: stat.size,
            content: fs.readFileSync(full, 'utf8'),
          });
        }
      }

      const desktopAppPath = path.join(PYTHON_DIR, 'desktop_app.py');
      if (fs.existsSync(desktopAppPath)) {
        const stat = fs.statSync(desktopAppPath);
        files.push({
          name: 'python_bot/desktop_app.py',
          size: stat.size,
          content: fs.readFileSync(desktopAppPath, 'utf8'),
        });
      }

      res.json({
        files,
        uiBundleReady: fs.existsSync(path.join(DIST_DIR, 'index.html')),
      });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Windows files read error' });
    }
  });

  let windowsPackage: Promise<Buffer> | undefined;
  const buildWindowsPackage = async () => {
    const { build } = await import('vite');
    await build();
    const zip = new JSZip();
    const root = zip.folder('SignalDesk_Windows')!;
    const addDirectory = (source: string, target: JSZip) => {
      for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
        if (entry.name.startsWith('.') || entry.name === '__pycache__' ||
            ['config.json', 'session.json', 'paper_lab_config.json', 'custom_assets.json'].includes(entry.name) || entry.name.endsWith('.pyc')) continue;
        const fullPath = path.join(source, entry.name);
        if (entry.isDirectory()) addDirectory(fullPath, target.folder(entry.name)!);
        else if (entry.isFile()) target.file(entry.name, fs.readFileSync(fullPath));
      }
    };
    addDirectory(WINDOWS_DIR, root);
    addDirectory(PYTHON_DIR, root.folder('python_bot')!);
    addDirectory(DIST_DIR, root.folder('ui_dist')!);
    return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  };

  app.post('/api/windows-download', async (_req, res) => {
    try {
      windowsPackage ??= buildWindowsPackage().finally(() => { windowsPackage = undefined; });
      const buffer = await windowsPackage;
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('Content-Type', 'application/zip');
      res.setHeader('Content-Disposition', 'attachment; filename="SignalDesk_Windows_Installer.zip"');
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Windows package error' });
    }
  });

  const buildCompleteSourceZip = async (customConfig?: Record<string, unknown>) => {
    const zip = new JSZip();
    const rootFolder = zip.folder('SignalDesk_Source_Code');
    if (!rootFolder) {
      throw new Error('Failed to initialize source archive folder');
    }

    const rootFiles = [
      'package.json',
      'tsconfig.json',
      'vite.config.ts',
      'index.html',
      'server.ts',
      'metadata.json',
      '.env.example',
      '.gitignore',
    ];
    for (const fileName of rootFiles) {
      const fullPath = path.join(ROOT_DIR, fileName);
      if (fs.existsSync(fullPath) && fs.statSync(fullPath).isFile()) {
        rootFolder.file(fileName, fs.readFileSync(fullPath));
      }
    }

    const excludedDirs = new Set(['node_modules', 'dist', '.git', '.aistudio', '__pycache__', '.venv', 'venv', 'aistudio']);
    const excludedExts = new Set(['.pyc', '.pyo']);

    const addSourceDir = (srcDir: string, targetFolder: JSZip, isPythonDir = false) => {
      if (!fs.existsSync(srcDir)) return;
      for (const entry of fs.readdirSync(srcDir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        if (excludedDirs.has(entry.name)) continue;
        if (entry.name.startsWith('.env') && entry.name !== '.env.example') continue;
        const fullPath = path.join(srcDir, entry.name);
        if (entry.isDirectory()) {
          const sub = targetFolder.folder(entry.name);
          if (sub) addSourceDir(fullPath, sub, isPythonDir);
        } else if (entry.isFile()) {
          if (excludedExts.has(path.extname(entry.name).toLowerCase())) continue;
          if (isPythonDir && entry.name === 'config.json' && customConfig) {
            targetFolder.file('config.json', JSON.stringify(customConfig, null, 2));
          } else {
            targetFolder.file(entry.name, fs.readFileSync(fullPath));
          }
        }
      }
    };

    const srcSub = rootFolder.folder('src');
    if (srcSub) addSourceDir(path.join(ROOT_DIR, 'src'), srcSub);

    const publicSub = rootFolder.folder('public');
    if (publicSub) addSourceDir(path.join(ROOT_DIR, 'public'), publicSub);

    const pySub = rootFolder.folder('python_bot');
    if (pySub) addSourceDir(PYTHON_DIR, pySub, true);

    const winSub = rootFolder.folder('windows_app');
    if (winSub) addSourceDir(WINDOWS_DIR, winSub);

    rootFolder.file(
      'README.md',
      [
        '# SignalDesk - Complete Source Code',
        '',
        'Full-stack Quotex Multi-Strategy Signal Bot (React + TypeScript + Express + Python Engine + Windows Desktop Installer).',
        '',
        '## 1. Run Full-Stack Web App Locally',
        '```bash',
        'npm install',
        'pip install -r python_bot/requirements.txt',
        'npm run dev',
        '```',
        'Then open http://localhost:3000',
        '',
        '## 2. Build Production Bundle',
        '```bash',
        'npm run build',
        '```',
        '',
        '## 3. Run Standalone Python Bot',
        '```bash',
        'cd python_bot',
        'pip install -r requirements.txt',
        'python bot.py',
        '```',
        '',
        '## 4. Install Optional pyquotex (Direct 1:1 Quotex Live Candles)',
        '```bash',
        'pip install --upgrade httpx requests websocket-client git+https://github.com/cleitonleonel/pyquotex.git',
        'playwright install chromium',
        '```',
        '',
        '## 5. Run Standalone Desktop App (No Node.js Required After Build)',
        '```bash',
        'npm run build',
        'python python_bot/desktop_app.py',
        '```',
      ].join('\n')
    );

    return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
  };

  app.get('/api/source-download', async (_req, res) => {
    try {
      const buffer = await buildCompleteSourceZip();
      res.setHeader('Content-Type', 'application/zip');
      res.setHeader(
        'Content-Disposition',
        'attachment; filename="SignalDesk_Complete_Source_Code.zip"'
      );
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Source archive error' });
    }
  });

  app.post('/api/source-download', async (req, res) => {
    try {
      const customConfig = req.body?.config as Record<string, unknown> | undefined;
      const buffer = await buildCompleteSourceZip(customConfig);
      res.setHeader('Content-Type', 'application/zip');
      res.setHeader(
        'Content-Disposition',
        'attachment; filename="SignalDesk_Complete_Source_Code.zip"'
      );
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : 'Source archive error' });
    }
  });

  // -------------------------------------------------------------------------
  // Persistent SQLite Paper-Trading Learning Lab API (/api/paper-lab/*)
  // Direct access — no sign-in required
  // -------------------------------------------------------------------------
  app.get('/api/paper-lab/overview', async (_req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'paper_lab_overview',
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Overview error' });
    }
  });

  app.get('/api/paper-lab/history', async (req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'paper_lab_history',
        table: req.query.table,
        from_ts: req.query.from_ts,
        to_ts: req.query.to_ts,
        asset: req.query.asset,
        strategy_version: req.query.strategy_version,
        feed_kind: req.query.feed_kind,
        limit: req.query.limit ? Number(req.query.limit) : 250,
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'History query error' });
    }
  });

  app.get('/api/paper-lab/export', async (req, res) => {
    try {
      const format = String(req.query.format || 'json').toLowerCase();
      const table = String(req.query.table || 'paper_trades');
      const data = (await runPythonEngine({
        action: 'paper_lab_export',
        table,
        format,
        from_ts: req.query.from_ts,
        to_ts: req.query.to_ts,
        asset: req.query.asset,
        strategy_version: req.query.strategy_version,
        feed_kind: req.query.feed_kind,
      })) as Record<string, unknown>;
      const content = String(data?.content ?? '');
      const ext = format === 'csv' ? 'csv' : 'json';
      res.setHeader('Content-Type', format === 'csv' ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8');
      res.setHeader('Content-Disposition', `attachment; filename="paper_lab_${table}.${ext}"`);
      res.send(content);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Export error' });
    }
  });

  app.get('/api/paper-lab/settings', async (_req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'paper_lab_get_settings',
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Settings read error' });
    }
  });

  app.post('/api/paper-lab/settings', async (req, res) => {
    try {
      const data = await runPythonEngine({
        action: 'paper_lab_update_settings',
        ...(req.body ?? {}),
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Settings save error' });
    }
  });

  app.post('/api/paper-lab/action/:op', async (req, res) => {
    const opMap: Record<string, string> = {
      cycle: 'paper_lab_run_cycle',
      retrain: 'paper_lab_retrain',
      backup: 'paper_lab_backup',
      rollback: 'paper_lab_rollback',
      resume: 'paper_lab_resume',
      verify: 'paper_lab_run_verification',
      'toggle-loop': 'paper_lab_toggle_autoloop',
    };
    const action = opMap[req.params.op];
    if (!action) {
      res.status(404).json({ ok: false, error: `Unknown Paper Lab operation: ${req.params.op}` });
      return;
    }
    try {
      const data = await runPythonEngine({
        action,
        ...(req.body ?? {}),
      });
      res.json(data);
    } catch (err) {
      res.status(500).json({ ok: false, error: err instanceof Error ? err.message : 'Paper Lab action failed' });
    }
  });

  const isProd = process.env.NODE_ENV === 'production';
  if (!isProd) {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distDir = path.join(ROOT_DIR, 'dist');
    app.use(express.static(distDir));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(distDir, 'index.html'));
    });
  }

  app.listen(PORT, '127.0.0.1', () => {
    console.log(`Quotex Signal Engine listening on http://127.0.0.1:${PORT}`);
  });
}

startServer();
