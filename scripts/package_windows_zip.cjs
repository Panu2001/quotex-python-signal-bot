const fs = require('fs');
const path = require('path');
const JSZip = require('jszip');

async function packageWindowsZip() {
  console.log('[1/3] Packaging SignalDesk Windows Installer ZIP...');
  const zip = new JSZip();
  const root = zip.folder('SignalDesk_Windows');

  const WINDOWS_DIR = path.join(__dirname, '..', 'windows_app');
  const PYTHON_DIR = path.join(__dirname, '..', 'python_bot');
  const DIST_DIR = path.join(__dirname, '..', 'dist');

  const addDirectory = (source, target) => {
    for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
      if (
        entry.name.startsWith('.') ||
        entry.name === '__pycache__' ||
        ['config.json', 'session.json', 'paper_lab_config.json', 'custom_assets.json'].includes(entry.name) ||
        entry.name.endsWith('.pyc')
      ) continue;
      const fullPath = path.join(source, entry.name);
      if (entry.isDirectory()) {
        addDirectory(fullPath, target.folder(entry.name));
      } else if (entry.isFile()) {
        target.file(entry.name, fs.readFileSync(fullPath));
      }
    }
  };

  addDirectory(WINDOWS_DIR, root);
  addDirectory(PYTHON_DIR, root.folder('python_bot'));
  addDirectory(DIST_DIR, root.folder('ui_dist'));

  console.log('[2/3] Compressing archive via DEFLATE...');
  const buffer = await zip.generateAsync({
    type: 'nodebuffer',
    compression: 'DEFLATE',
    compressionOptions: { level: 9 },
  });

  const rootZipPath = path.join(__dirname, '..', 'SignalDesk_Windows_Installer.zip');
  const distZipPath = path.join(DIST_DIR, 'SignalDesk_Windows_Installer.zip');

  fs.writeFileSync(rootZipPath, buffer);
  if (fs.existsSync(DIST_DIR)) {
    fs.writeFileSync(distZipPath, buffer);
  }

  console.log('[3/3] Successfully generated:');
  console.log(`      - ${rootZipPath} (${(buffer.length / (1024 * 1024)).toFixed(2)} MB)`);
  console.log(`      - ${distZipPath}`);
}

packageWindowsZip().catch((err) => {
  console.error(err);
  process.exit(1);
});
